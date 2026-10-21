"""Sensitivity analysis.

This is the part that makes the tool worth running. A pass rate on its own is
a leaderboard number; the *spread* across harness configurations is what tells
you whether a model's score reflects capability or just a well-tuned prompt.

Definitions used throughout:

``pass@k``   — probability at least one of k trials passes. Capability ceiling.
``pass^k``   — probability all k trials pass. Reliability floor.
``delta``    — pass rate of a harness minus the baseline harness, same model.
``HSI``      — Harness Sensitivity Index: mean absolute delta over all
               non-baseline harnesses. High HSI means the model's measured
               quality is mostly a property of the harness.
``flakiness``— 1 - pass^k / pass@k within one harness. Same config, same task,
               different answer.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .spec import TrialResult


# --------------------------------------------------------------------------
# Statistics helpers
# --------------------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval. Correct for small n, unlike normal approx.

    With 5 trials per cell the interval is wide — that is the honest answer and
    the report shows it rather than pretending a 60% vs 40% gap is real.
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimator of pass@k (Chen et al. 2021)."""
    if n == 0 or k <= 0:
        return 0.0
    if c == 0:
        return 0.0
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_pow_k(n: int, c: int, k: int) -> float:
    """Probability that all k sampled trials pass (reliability floor)."""
    if n == 0 or k <= 0 or k > n:
        return 0.0
    if c < k:
        return 0.0
    return math.comb(c, k) / math.comb(n, k)


# --------------------------------------------------------------------------
# Cell aggregation
# --------------------------------------------------------------------------


@dataclass
class Cell:
    """One (task, harness) pair, aggregated over trials."""

    task_id: str
    harness: str
    n: int = 0
    passes: int = 0
    tokens: int = 0
    wall_ms: int = 0
    steps: int = 0
    stop_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passes / self.n if self.n else 0.0

    @property
    def ci(self) -> tuple[float, float]:
        return wilson_interval(self.passes, self.n)

    @property
    def mean_tokens(self) -> float:
        return self.tokens / self.n if self.n else 0.0

    @property
    def mean_steps(self) -> float:
        return self.steps / self.n if self.n else 0.0


def aggregate(results: Iterable[TrialResult]) -> dict[tuple[str, str], Cell]:
    cells: dict[tuple[str, str], Cell] = {}
    for r in results:
        key = (r.task_id, r.harness)
        cell = cells.setdefault(key, Cell(task_id=r.task_id, harness=r.harness))
        cell.n += 1
        cell.passes += int(r.passed)
        cell.tokens += r.total_tokens
        cell.wall_ms += r.wall_ms
        cell.steps += len([s for s in r.steps if s.kind == "assistant"])
        cell.stop_reasons[r.stop_reason] = cell.stop_reasons.get(r.stop_reason, 0) + 1
        if r.error:
            cell.errors.append(r.error)
    return cells


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass
class HarnessRow:
    harness: str
    n: int
    pass_rate: float
    ci_low: float
    ci_high: float
    delta: float  # vs baseline, absolute percentage points
    mean_tokens: float
    token_delta_pct: float
    mean_steps: float
    flakiness: float
    per_task: dict[str, float] = field(default_factory=dict)


@dataclass
class Report:
    model: str
    baseline: str
    trials_per_cell: int
    rows: list[HarnessRow] = field(default_factory=list)
    hsi: float = 0.0
    hsi_worst: str = ""
    hsi_best: str = ""
    overall_pass_rate: float = 0.0
    task_count: int = 0
    warnings: list[str] = field(default_factory=list)
    knobs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "baseline": self.baseline,
            "trials_per_cell": self.trials_per_cell,
            "overall_pass_rate": self.overall_pass_rate,
            "task_count": self.task_count,
            "hsi": self.hsi,
            "hsi_worst": self.hsi_worst,
            "hsi_best": self.hsi_best,
            "warnings": self.warnings,
            "knobs": self.knobs,
            "harnesses": [
                {
                    "harness": r.harness,
                    "n": r.n,
                    "pass_rate": round(r.pass_rate, 4),
                    "ci": [round(r.ci_low, 4), round(r.ci_high, 4)],
                    "delta": round(r.delta, 4),
                    "mean_tokens": round(r.mean_tokens, 1),
                    "token_delta_pct": round(r.token_delta_pct, 2),
                    "mean_steps": round(r.mean_steps, 2),
                    "flakiness": round(r.flakiness, 4),
                    "per_task": {k: round(v, 4) for k, v in sorted(r.per_task.items())},
                }
                for r in self.rows
            ],
        }


def analyze(
    results: list[TrialResult],
    *,
    model: str,
    baseline: str,
    harness_knobs: dict[str, dict[str, Any]] | None = None,
    k: int = 1,
) -> Report:
    cells = aggregate(results)
    if not cells:
        return Report(model=model, baseline=baseline, trials_per_cell=0)

    by_harness: dict[str, list[Cell]] = defaultdict(list)
    for (task_id, harness), cell in cells.items():
        by_harness[harness].append(cell)

    # Trials per cell can differ if a run was interrupted; report the min.
    trials_per_cell = min(c.n for c in cells.values())
    task_count = len({c.task_id for c in cells.values()})

    base_cells = by_harness.get(baseline)
    if base_cells is None:
        raise ValueError(f"baseline harness {baseline!r} not present in results")
    base_rate = sum(c.passes for c in base_cells) / max(1, sum(c.n for c in base_cells))
    base_tokens = sum(c.tokens for c in base_cells) / max(1, sum(c.n for c in base_cells))

    rows: list[HarnessRow] = []
    for harness, hcells in sorted(by_harness.items()):
        n = sum(c.n for c in hcells)
        passes = sum(c.passes for c in hcells)
        tokens = sum(c.tokens for c in hcells)
        rate = passes / n if n else 0.0
        low, high = wilson_interval(passes, n)

        # Flakiness: average over tasks of (1 - pass^k / pass@k).
        flake_terms = []
        for c in hcells:
            pak = pass_at_k(c.n, c.passes, k)
            ppk = pass_pow_k(c.n, c.passes, k)
            if pak > 0:
                flake_terms.append(1.0 - ppk / pak)
        flakiness = sum(flake_terms) / len(flake_terms) if flake_terms else 0.0

        rows.append(
            HarnessRow(
                harness=harness,
                n=n,
                pass_rate=rate,
                ci_low=low,
                ci_high=high,
                delta=rate - base_rate,
                mean_tokens=tokens / n if n else 0.0,
                token_delta_pct=((tokens / n) - base_tokens) / base_tokens * 100
                if base_tokens
                else 0.0,
                mean_steps=sum(c.steps for c in hcells) / n if n else 0.0,
                flakiness=flakiness,
                per_task={c.task_id: c.pass_rate for c in hcells},
            )
        )

    non_base = [r for r in rows if r.harness != baseline]
    hsi = sum(abs(r.delta) for r in non_base) / len(non_base) if non_base else 0.0
    worst = max(non_base, key=lambda r: r.delta, default=None)
    best = min(non_base, key=lambda r: r.delta, default=None)

    warnings: list[str] = []
    if trials_per_cell < 3:
        warnings.append(
            f"only {trials_per_cell} trial(s) per cell — confidence intervals are "
            "very wide; use --trials 3 or more before drawing conclusions"
        )
    if any(r.ci_high - r.ci_low > 0.5 for r in rows):
        warnings.append(
            "at least one cell has a CI wider than 0.5; the sample is too small "
            "to separate harnesses that differ by less than ~30 points"
        )
    if task_count < 5:
        warnings.append(
            f"only {task_count} task(s) — a single unlucky task dominates the mean"
        )

    return Report(
        model=model,
        baseline=baseline,
        trials_per_cell=trials_per_cell,
        rows=rows,
        hsi=hsi,
        hsi_worst=worst.harness if worst else "",
        hsi_best=best.harness if best else "",
        overall_pass_rate=sum(c.passes for c in cells.values())
        / max(1, sum(c.n for c in cells.values())),
        task_count=task_count,
        warnings=warnings,
        knobs=harness_knobs or {},
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_markdown(report: Report) -> str:
    lines = [
        f"# HarnessProbe report — `{report.model}`",
        "",
        f"- baseline harness: **{report.baseline}**",
        f"- tasks: {report.task_count}  ·  trials per cell: {report.trials_per_cell}",
        f"- overall pass rate: {report.overall_pass_rate:.1%}",
        f"- **Harness Sensitivity Index: {report.hsi:.1%}**"
        + (f"  (most sensitive to `{report.hsi_worst}`, most robust under `{report.hsi_best}`)"
           if report.hsi_worst else ""),
        "",
        "| harness | pass rate | delta vs baseline | 95% CI | tokens | token delta | steps | flakiness |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in sorted(report.rows, key=lambda r: -r.pass_rate):
        # ASCII only: reports get printed to Windows consoles with a GBK codepage.
        mark = " (baseline)" if row.harness == report.baseline else ""
        lines.append(
            f"| `{row.harness}`{mark} | {row.pass_rate:.1%} | {row.delta:+.1%} | "
            f"[{row.ci_low:.0%}, {row.ci_high:.0%}] | {row.mean_tokens:.0f} | "
            f"{row.token_delta_pct:+.1f}% | {row.mean_steps:.1f} | {row.flakiness:.2f} |"
        )
    if report.warnings:
        lines += ["", "## Caveats", ""]
        lines += [f"- {w}" for w in report.warnings]
    return "\n".join(lines) + "\n"


def render_per_task(report: Report) -> str:
    """Task x harness matrix — where a harness helps and where it hurts."""
    if not report.rows:
        return ""
    tasks = sorted({t for r in report.rows for t in r.per_task})
    if not tasks:
        return ""
    header = "| task | " + " | ".join(f"`{r.harness}`" for r in report.rows) + " |"
    sep = "|---|" + "---:|" * len(report.rows)
    lines = [header, sep]
    for task in tasks:
        cells = []
        for r in report.rows:
            v = r.per_task.get(task)
            cells.append("-" if v is None else f"{v:.0%}")
        lines.append(f"| `{task}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
