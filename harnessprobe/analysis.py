"""Sensitivity analysis.

This is the part that makes the tool worth running. A pass rate on its own is
a leaderboard number; the *spread* across harness configurations is what tells
you whether a model's score reflects capability or just a well-tuned prompt.

Definitions used throughout:

``pass@k``     — probability at least one of k trials passes. Capability ceiling.
``pass^k``     — probability all k trials pass. Reliability floor.
``delta``      — pass rate of a harness minus the baseline harness, same model.
``HSI``        — Harness Sensitivity Index: mean absolute delta over all
                 non-baseline harnesses. A measurement under this task set,
                 harness matrix and budget — not a property of the model.
``max degradation`` — the single largest drop. Reported alongside HSI because
                 averaging over harnesses dilutes one large regression.
``paired delta``— per-task delta against the baseline. The task is the
                 independent unit; trials of one task are not independent
                 samples.
``flakiness``  — 1 - pass^k / pass@k within one harness. Same config, same task,
                 different answer. Only meaningful for k > 1.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

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
    # None when k <= 1: the quantity is not defined, and 0.00 would read as
    # "measured stable" rather than "not measured".
    flakiness: float | None
    per_task: dict[str, float] = field(default_factory=dict)
    # How trials ended, e.g. {"submitted": 3, "max_steps": 1}. The shape of
    # the failures is what separates "ran out of budget" from "could not do
    # it", which a pass rate cannot distinguish.
    stop_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class Report:
    model: str
    baseline: str
    trials_per_cell: int
    rows: list[HarnessRow] = field(default_factory=list)
    hsi: float = 0.0
    # Worst = largest measured drop vs baseline; best = largest measured gain.
    # Both are descriptive, not causal: they name the configuration associated
    # with the biggest movement, not the reason for it.
    hsi_worst: str = ""
    hsi_best: str = ""
    # Largest single drop vs baseline, in percentage points. HSI averages over
    # harnesses and dilutes one large regression into noise — ten harnesses at
    # 0 pp plus one at -16 pp gives an HSI of 1.7%, which reads as "not
    # sensitive". This field cannot hide that.
    max_degradation: float = 0.0
    # Per-task delta in percentage points, keyed by harness name. Pairing by
    # task is what makes a delta interpretable.
    paired: dict[str, dict[str, float]] = field(default_factory=dict)
    # Task-level bootstrap CI for the mean paired delta, per harness.
    paired_ci: dict[str, tuple[float, float]] = field(default_factory=dict)
    overall_pass_rate: float = 0.0
    # False when the run did not cover every (task, harness) cell. Aggregate
    # numbers from an incomplete matrix must not be read as a full experiment.
    complete: bool = True
    task_count: int = 0
    warnings: list[str] = field(default_factory=list)
    knobs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "baseline": self.baseline,
            "trials_per_cell": self.trials_per_cell,
            "overall_pass_rate": self.overall_pass_rate,
            "complete": self.complete,
            "task_count": self.task_count,
            "hsi": self.hsi,
            "hsi_worst": self.hsi_worst,
            "hsi_best": self.hsi_best,
            "max_degradation": round(self.max_degradation, 4),
            "paired": {
                h: {t: round(v, 4) for t, v in sorted(d.items())}
                for h, d in self.paired.items()
            },
            "paired_ci": {
                h: [round(lo, 4), round(hi, 4)]
                for h, (lo, hi) in self.paired_ci.items()
            },
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
                    "flakiness": None if r.flakiness is None else round(r.flakiness, 4),
                    "per_task": {k: round(v, 4) for k, v in sorted(r.per_task.items())},
                }
                for r in self.rows
            ],
        }


def paired_deltas(
    results: list[TrialResult], baseline: str
) -> dict[str, dict[str, float]]:
    """Per-task pass-rate difference between each harness and the baseline.

    Pairing by task is what makes a delta interpretable: a harness that loses
    points on hard tasks and a harness that loses them uniformly are different
    findings, and the aggregate hides which one you have.
    """
    cells = aggregate(results)
    base = {
        task: cells[(task, baseline)]
        for (_task, h), cell in cells.items()
        if h == baseline
        for task in [_task]
    }
    out: dict[str, dict[str, float]] = {}
    for (task, harness), cell in cells.items():
        if harness == baseline or task not in base:
            continue
        out.setdefault(harness, {})[task] = cell.pass_rate - base[task].pass_rate
    return out


def task_level_bootstrap_ci(
    deltas: dict[str, float], *, iterations: int = 2000, alpha: float = 0.05
) -> tuple[float, float]:
    """Bootstrap CI over tasks for the mean of a per-task delta vector.

    The task is the independent unit, not the trial: three trials of the same
    task are three measurements of one item, and treating them as independent
    understates the interval by roughly the number of trials. Wilson is still
    the right tool for a single pass *rate*; this is for a difference of rates.
    """
    values = list(deltas.values())
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (values[0], values[0])
    # Deterministic pseudo-sampling: seed from the data itself so the same
    # deltas always produce the same interval (reports must be reproducible).
    rng = _DeterministicRandom(values, n)
    means: list[float] = []
    for _ in range(iterations):
        sample = [values[rng.next_index()] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return (lo, hi)


class _DeterministicRandom:
    """A tiny LCG. Enough for bootstrap resampling, and reproducible."""

    def __init__(self, seed_values: list[float], modulus: int) -> None:
        # hash() is salted per process, so derive the seed from the values
        # themselves — a report must be byte-identical when re-run.
        digest = hashlib.sha256(
            json.dumps(seed_values, sort_keys=True).encode("utf-8")
        ).digest()
        self.state = int.from_bytes(digest[:4], "big") or 1
        self.modulus = max(1, modulus)

    def next_index(self) -> int:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state % self.modulus


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
    base_tokens = sum(c.tokens for c in base_cells) / max(
        1, sum(c.n for c in base_cells)
    )

    rows: list[HarnessRow] = []
    for harness, hcells in sorted(by_harness.items()):
        n = sum(c.n for c in hcells)
        passes = sum(c.passes for c in hcells)
        tokens = sum(c.tokens for c in hcells)
        rate = passes / n if n else 0.0
        low, high = wilson_interval(passes, n)

        # Flakiness: average over tasks of (1 - pass^k / pass@k).
        # At k=1 this is identically zero, so it is reported as None rather than
        # 0.00 — a zero implies "measured and stable", which k=1 cannot support.
        flake_terms = []
        for c in hcells:
            pak = pass_at_k(c.n, c.passes, k)
            ppk = pass_pow_k(c.n, c.passes, k)
            if pak > 0:
                flake_terms.append(1.0 - ppk / pak)
        flakiness: float | None = None
        if k > 1:
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
                stop_reasons=dict(
                    sorted(
                        {
                            reason: sum(c.stop_reasons.get(reason, 0) for c in hcells)
                            for reason in {r for c in hcells for r in c.stop_reasons}
                        }.items()
                    )
                ),
                per_task={c.task_id: c.pass_rate for c in hcells},
            )
        )

    non_base = [r for r in rows if r.harness != baseline]
    hsi = sum(abs(r.delta) for r in non_base) / len(non_base) if non_base else 0.0

    # Naming: `delta` is signed, so the most *damaging* harness is the one with
    # the LOWEST delta (largest drop), not the highest. Naming them by |delta|
    # would report the biggest improver as the biggest regression whenever the
    # baseline is not the best-performing configuration.
    #
    # With a single non-baseline harness it is both the max and the min, so the
    # labels would say the same thing twice. Leave them empty in that case
    # rather than asserting a direction the data does not support.
    if len(non_base) >= 2:
        worst = min(non_base, key=lambda r: r.delta)
        best = max(non_base, key=lambda r: r.delta)
    else:
        worst = None
        best = None

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

    # Max degradation: the single largest drop. HSI averages over harnesses and
    # dilutes one large regression into noise (10 harnesses at 0 pp plus one at
    # -16 pp yields an HSI of 1.7%, which reads as "not sensitive").
    max_degradation = min((r.delta for r in non_base), default=0.0)

    # Paired, per-task deltas with a task-level bootstrap interval.
    paired = paired_deltas(results, baseline)
    paired_ci: dict[str, tuple[float, float]] = {}
    for harness, deltas in paired.items():
        if len(deltas) >= 2:
            paired_ci[harness] = task_level_bootstrap_ci(deltas)
        else:
            paired_ci[harness] = (0.0, 0.0)

    return Report(
        model=model,
        baseline=baseline,
        trials_per_cell=trials_per_cell,
        rows=rows,
        hsi=hsi,
        hsi_worst=worst.harness if worst is not None else "",
        hsi_best=best.harness if best is not None else "",
        max_degradation=max_degradation,
        paired=paired,
        paired_ci=paired_ci,
        overall_pass_rate=sum(c.passes for c in cells.values())
        / max(1, sum(c.n for c in cells.values())),
        task_count=task_count,
        warnings=warnings,
        knobs=harness_knobs or {},
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Diagnosis
# --------------------------------------------------------------------------

# Each entry maps a knob to what its signature looks like in the data. The
# signature is a *hypothesis*, deliberately: a drop under `tight-budget` whose
# trials end in max_steps is evidence for a budget hypothesis, not proof of
# one — the same pattern appears when a task is near the model's limit and any
# perturbation tips it.
_KNOB_SIGNATURES: list[dict[str, Any]] = [
    {
        "knob": "max_steps",
        "harnesses": ("tight-budget", "loose-budget"),
        "stop_reason": "max_steps",
        "hypothesis": "the step budget is binding — trials run out of room "
        "before they finish",
        "experiment": "fix every other knob and sweep max_steps across "
        "several values; a dose-response curve (more steps, monotonically more "
        "passes) supports the hypothesis, a flat one refutes it",
    },
    {
        "knob": "tool_set (shell access)",
        "harnesses": ("no-shell",),
        "stop_reason": None,
        "hypothesis": "the agent cannot verify its own work, so it cannot "
        "detect or recover from its own mistakes",
        "experiment": "compare no-shell against a harness that keeps the shell "
        "but hides its output; if the drop persists, the loss is verification, "
        "not execution",
    },
    {
        "knob": "tool_set (search)",
        "harnesses": ("no-search",),
        "stop_reason": None,
        "hypothesis": "the agent must read files to locate code, so it spends "
        "its budget on navigation instead of the fix",
        "experiment": "compare no-search against full tools on tasks whose "
        "code is spread over many files; if the gap grows with file count, the "
        "cost is navigation",
    },
    {
        "knob": "tool_description_style",
        "harnesses": ("minimal-tools", "terse-tools"),
        "stop_reason": None,
        "hypothesis": "the agent misuses the tools because it cannot see how "
        "they behave",
        "experiment": "inspect the failed traces for malformed tool calls or "
        "wrong argument shapes; if the failure mode is tool misuse rather than "
        "a wrong answer, the hypothesis holds",
    },
    {
        "knob": "history_policy",
        "harnesses": ("drop-old-output",),
        "stop_reason": None,
        "hypothesis": "the agent loses information it gathered earlier and "
        "re-derives or contradicts it",
        "experiment": "compare against keep_last_n with a matched window size; "
        "if both degrade similarly, the loss is context volume rather than "
        "which content was dropped",
    },
    {
        "knob": "reasoning_hint",
        "harnesses": ("no-reasoning-hint",),
        "stop_reason": None,
        "hypothesis": "the agent acts before it has understood the task",
        "experiment": "compare step counts and first-action correctness; if "
        "the no-hint run takes more steps to reach the same fix, the hint was "
        "buying planning, not verbosity",
    },
    {
        "knob": "retry_on_error",
        "harnesses": ("no-retry-nudge",),
        "stop_reason": "no_tool_call",
        "hypothesis": "the agent stops calling tools and the session ends "
        "before the work is done",
        "experiment": "count trials ending in no_tool_call under both "
        "harnesses; if the drop is entirely explained by those, the knob is "
        "recovering stalls rather than improving capability",
    },
    {
        "knob": "system_prompt",
        "harnesses": ("verbose-system",),
        "stop_reason": None,
        "hypothesis": "the system prompt changed what the agent prioritises",
        "experiment": "ablate the prompt's clauses one at a time; a single "
        "clause carrying the whole effect is a different finding from the "
        "prompt as a whole",
    },
]


def diagnose(report: Report) -> list[dict[str, Any]]:
    """Attach a hypothesis and a confirming experiment to each degradation.

    Returns one entry per harness whose delta is negative, largest drop first.
    Every entry is explicitly a hypothesis: the data shows *that* a
    configuration is associated with a drop, not *why*. The experiment field
    is what would settle it.

    A harness with no matching signature still gets an entry — "no hypothesis"
    is an honest result and better than inventing a mechanism.
    """
    out: list[dict[str, Any]] = []
    rows = {r.harness: r for r in report.rows}
    baseline_row = rows.get(report.baseline)

    for row in sorted(report.rows, key=lambda r: r.delta):
        if row.harness == report.baseline or row.delta >= 0:
            continue

        signature = next(
            (s for s in _KNOB_SIGNATURES if row.harness in s["harnesses"]), None
        )
        entry: dict[str, Any] = {
            "harness": row.harness,
            "delta": row.delta,
            "stop_reasons": row.stop_reasons,
            "knob": signature["knob"] if signature else None,
            "hypothesis": signature["hypothesis"] if signature else None,
            "experiment": signature["experiment"] if signature else None,
            "evidence": [],
        }

        # Does the stop-reason distribution support the signature?
        if signature and signature.get("stop_reason"):
            reason = signature["stop_reason"]
            here = row.stop_reasons.get(reason, 0)
            there = baseline_row.stop_reasons.get(reason, 0) if baseline_row else 0
            if here > there:
                entry["evidence"].append(
                    f"{reason} occurred {here}x here vs {there}x at baseline"
                )
            else:
                entry["evidence"].append(
                    f"expected more {reason} endings, saw {here} vs {there} at "
                    "baseline — the stop-reason evidence does not support this "
                    "hypothesis"
                )

        if signature:
            changed = report.knobs.get(row.harness, {})
            diff = {
                k: v
                for k, v in changed.items()
                if baseline_row is None
                or v != report.knobs.get(report.baseline, {}).get(k)
            }
            if diff:
                entry["evidence"].append(
                    "knob actually changed: "
                    + ", ".join(f"{k}={v!r}" for k, v in sorted(diff.items()))
                )

        if not entry["evidence"]:
            entry["evidence"].append(
                "no supporting or contradicting evidence beyond the delta itself"
            )
        out.append(entry)
    return out


def render_diagnosis(report: Report) -> str:
    """Render the diagnosis section, hypotheses labelled as hypotheses."""
    entries = diagnose(report)
    if not entries:
        return ""

    lines = [
        "## Diagnosis (hypotheses, not conclusions)",
        "",
        (
            "Each entry names the knob most likely responsible and the "
            "experiment that would confirm it. A drop is evidence that a "
            "configuration is associated with a failure, not evidence of why "
            "— at least three mechanisms produce the same delta."
        ),
        "",
    ]
    for entry in entries:
        lines.append(f"### `{entry['harness']}`  ({entry['delta']:+.1%})")
        lines.append("")
        if entry["knob"]:
            lines.append(f"- **Knob:** `{entry['knob']}`")
            lines.append(f"- **Hypothesis:** {entry['hypothesis']}")
            lines.append(f"- **To confirm:** {entry['experiment']}")
        else:
            lines.append(
                "- **Knob:** no signature matched this harness. That is an "
                "honest result — read the failed traces before inventing a "
                "mechanism."
            )
        reasons = ", ".join(f"{k}x {v}" for k, v in entry["stop_reasons"].items())
        if reasons:
            lines.append(f"- **How trials ended:** {reasons}")
        lines.append("- **Evidence:**")
        lines.extend(f"  - {e}" for e in entry["evidence"])
        lines.append("")
    return "\n".join(lines)


def render_markdown(report: Report) -> str:
    lines = [
        f"# HarnessProbe report — `{report.model}`",
        "",
        f"- baseline harness: **{report.baseline}**",
        f"- tasks: {report.task_count}  ·  trials per cell: {report.trials_per_cell}",
        f"- overall pass rate: {report.overall_pass_rate:.1%}",
        f"- **Harness Sensitivity Index: {report.hsi:.1%}**"
        + (
            f"  (largest drop under `{report.hsi_worst}`, "
            f"largest gain under `{report.hsi_best}`)"
            if report.hsi_worst
            else ""
        ),
        f"- **Max degradation: {report.max_degradation:+.1%}**"
        + (
            f" under `{report.hsi_worst}`"
            if report.hsi_worst and report.max_degradation < 0
            else ""
        ),
        "",
        (
            "HSI is a mean over harnesses and hides a single large regression; "
            "read it together with max degradation. Both are descriptive "
            "measurements under this task set, harness matrix and budget — not "
            "properties of the model."
        ),
        "",
        "| harness | pass rate | delta vs baseline | 95% CI | paired delta (task-level bootstrap) | tokens | token delta | steps |",
        "|---|---:|---:|---|---|---:|---:|---:|",
    ]
    for row in sorted(report.rows, key=lambda r: -r.pass_rate):
        # ASCII only: reports get printed to Windows consoles with a GBK codepage.
        mark = " (baseline)" if row.harness == report.baseline else ""
        lo, hi = report.paired_ci.get(row.harness, (0.0, 0.0))
        if report.paired.get(row.harness) and len(report.paired[row.harness]) >= 2:
            ci_text = f"{row.delta:+.1%} [{lo:+.0%}, {hi:+.0%}]"
        else:
            ci_text = f"{row.delta:+.1%} (n/a: <2 tasks)"
        lines.append(
            f"| `{row.harness}`{mark} | {row.pass_rate:.1%} | {row.delta:+.1%} | "
            f"[{row.ci_low:.0%}, {row.ci_high:.0%}] | {ci_text} | {row.mean_tokens:.0f} | "
            f"{row.token_delta_pct:+.1f}% | {row.mean_steps:.1f} |"
        )
    flaky = [r for r in report.rows if r.flakiness is not None]
    if flaky:
        lines += ["", "## Flakiness (k > 1)", ""]
        lines += ["| harness | flakiness |", "|---|---:|"]
        for row in sorted(flaky, key=lambda r: -(r.flakiness or 0.0)):
            lines.append(f"| `{row.harness}` | {row.flakiness:.2f} |")
        lines += [
            "",
            (
                "Same config, same task, different outcome. High flakiness "
                "means the measurement is noisy before any harness change is "
                "considered."
            ),
        ]
    elif report.trials_per_cell <= 1:
        lines += [
            "",
            (
                "Flakiness is not reported: it needs k > 1 trials per cell, and "
                "this run has one. Run with `--trials 3` or more to measure it."
            ),
        ]

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
