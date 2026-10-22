"""Command line entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import (
    analyze,
    render_diagnosis,
    render_markdown,
    render_per_task,
)
from .graders import grade
from .providers import build_provider
from .runner import MatrixJob, run_matrix, summarize_trace
from .spec import HarnessConfig, RunManifest, SpecError, Task, TrialResult

DEFAULT_RUNS_DIR = Path(".harnessprobe") / "runs"


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_tasks(paths: list[str]) -> list[Task]:
    """Load tasks from directories, task.json files, or a parent dir of both."""
    tasks: list[Task] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            raise SpecError(f"task path does not exist: {p}")
        candidates: list[Path] = []
        if (p / "task.json").exists():
            candidates = [p]
        elif p.is_file() and p.name == "task.json":
            candidates = [p.parent]
        else:
            candidates = sorted(d.parent for d in p.glob("*/task.json"))
        if not candidates:
            raise SpecError(f"no task.json found under {p}")
        for c in candidates:
            task = Task.load(c)
            if task.id in seen:
                raise SpecError(f"duplicate task id {task.id!r} (in {c})")
            seen.add(task.id)
            tasks.append(task)
    return tasks


def load_harnesses(path: str) -> tuple[list[HarnessConfig], str]:
    """Load a matrix file. Returns (configs with knobs merged, baseline name)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    baseline_raw = raw.get("baseline")
    if not baseline_raw:
        raise SpecError("matrix file needs a 'baseline' harness object")
    baseline = HarnessConfig.from_dict(baseline_raw)
    baseline.name = baseline.name or "baseline"
    baseline.validate()

    merged: list[HarnessConfig] = [baseline]
    for item in raw.get("harnesses", []):
        cfg = HarnessConfig.from_dict(item).merged(baseline)
        cfg.validate()
        merged.append(cfg)

    names = [c.name for c in merged]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SpecError(f"duplicate harness names: {sorted(dupes)}")
    return merged, baseline.name


def experiment_fingerprint(
    tasks: list[Task], harnesses: list[HarnessConfig], baseline: str, trials: int
) -> str:
    """Hash everything a resumed run must match to reuse its earlier trials.

    Resuming reads trials recorded by a previous invocation and treats them as
    results of *this* experiment. If the task set, a prompt, a harness knob or
    the trial count changed in between, those cached trials describe a
    different experiment — and a report assembled from both would look complete
    while measuring something else. Comparing this fingerprint before reusing
    anything turns that into a refusal instead.
    """
    blob = json.dumps(
        {
            "tasks": [
                {
                    "id": t.id,
                    "prompt": t.prompt,
                    "max_steps": t.max_steps,
                    "grader": asdict(t.grader),
                    "readonly": sorted(t.readonly),
                }
                for t in tasks
            ],
            "harnesses": [h.to_dict() for h in harnesses],
            "baseline": baseline,
            "trials": trials,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    harnesses, baseline_name = load_harnesses(args.matrix)
    provider = build_provider(
        args.provider,
        args.model,
        base_url=args.base_url,
        request_delay=args.delay,
        mock_script=args.mock_script,
    )

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = experiment_fingerprint(tasks, harnesses, baseline_name, args.trials)

    # Capture the previous manifest's fingerprint BEFORE overwriting it, so a
    # resume can tell whether the cached trials came from this same experiment.
    prior_fingerprint = ""
    prior_manifest_path = run_dir / "manifest.json"
    if prior_manifest_path.exists():
        try:
            prior_fingerprint = json.loads(
                prior_manifest_path.read_text(encoding="utf-8")
            ).get("fingerprint", "")
        except (json.JSONDecodeError, OSError):
            prior_fingerprint = ""

    manifest = RunManifest(
        run_id=run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        provider=args.provider,
        model=args.model,
        trials=args.trials,
        task_ids=[t.id for t in tasks],
        baseline=baseline_name,
        harnesses=[h.name for h in harnesses],
        harnessprobe_version=__version__,
        notes=args.notes,
        fingerprint=fingerprint,
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = len(tasks) * len(harnesses) * args.trials
    counter = {"done": 0, "passed": 0}
    trace_path = run_dir / "traces.jsonl"
    # on_result is called from worker threads: serialise the counter, the file
    # append, and the print so progress lines stay one per trial.
    lock = threading.Lock()

    # Resuming reads what traces.jsonl already holds and skips those units, so an
    # interrupted run (or one cut short by a provider outage) continues without
    # re-spending tokens. Only successful trials count as done: a trial that died
    # on a provider error is not a result and must be re-run.
    resumed: list[TrialResult] = []
    done_keys: set[tuple[str, str, int]] = set()
    if args.resume and trace_path.exists():
        # Refuse to reuse trials from a different experiment. Without this, a
        # changed matrix or task set would silently absorb the old results and
        # the report would look complete while measuring something else.
        if prior_fingerprint and prior_fingerprint != fingerprint:
            print(
                "error: this run was recorded with a different experiment "
                f"(fingerprint {prior_fingerprint}, now {fingerprint}).\n"
                "       Reusing its trials would mix results from two different "
                "experiments.\n"
                "       Start a new run with --run-id, or revert the matrix / "
                "task changes.",
                file=sys.stderr,
            )
            return 2

        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                prior_trial = TrialResult.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
            if prior_trial.error:
                continue
            done_keys.add((prior_trial.task_id, prior_trial.harness, prior_trial.trial))
            resumed.append(prior_trial)
        counter["done"] = len(resumed)
        counter["passed"] = sum(int(r.passed) for r in resumed)
        if resumed:
            print(f"resuming: {len(resumed)} trial(s) already recorded")

    def on_result(r: TrialResult) -> None:
        with lock:
            counter["done"] += 1
            counter["passed"] += int(r.passed)
            with trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
            flag = "PASS" if r.passed else "FAIL"
            print(
                f"[{counter['done']}/{total}] {flag} {r.task_id} / {r.harness} / t{r.trial} "
                f"({r.total_tokens} tok, {r.wall_ms}ms, stop={r.stop_reason})",
                flush=True,
            )

    job = MatrixJob(
        tasks=tasks,
        harnesses=harnesses,
        provider=provider,
        trials=args.trials,
        work_dir=run_dir / "work",
        max_tokens=args.max_tokens,
        keep_sandboxes=args.keep_sandboxes,
        grade=lambda task, result, sandbox: grade(task, result, sandbox),
        concurrency=args.concurrency,
    )

    print(
        f"run {run_id}: {len(tasks)} tasks x {len(harnesses)} harnesses x {args.trials} trials = {total}"
    )
    started = time.time()
    outcome = run_matrix(job, on_result=on_result, skip=done_keys)
    results = resumed + outcome.results
    elapsed = time.time() - started

    (run_dir / "results.json").write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = analyze(
        results,
        model=args.model,
        baseline=baseline_name,
        harness_knobs={h.name: h.to_dict() for h in harnesses},
    )

    # An incomplete matrix must not be able to pass as a complete one. Without
    # this, a run cut short by a provider outage produces a report whose
    # aggregate numbers look like a full experiment.
    expected_cells = len(tasks) * len(harnesses)
    present_cells = len({(r.task_id, r.harness) for r in results})
    if present_cells < expected_cells:
        report.warnings.insert(
            0,
            f"INCOMPLETE EXPERIMENT: {present_cells}/{expected_cells} "
            "(task, harness) cells present. The aggregate numbers below do not "
            "describe a full run — per-task coverage varies and comparisons "
            "between harnesses are not apples-to-apples.",
        )
        report.complete = False
    else:
        report.complete = True

    (run_dir / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = (
        render_markdown(report)
        + "\n"
        + render_per_task(report)
        + "\n"
        + render_diagnosis(report)
    )
    (run_dir / "report.md").write_text(md, encoding="utf-8")

    print()
    print(md)
    print(
        f"elapsed {elapsed:.1f}s · pass {counter['passed']}/{counter['done']} · "
        f"results in {run_dir}"
    )
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Re-grade an existing run without re-spending tokens."""
    run_dir = Path(args.run)
    results_path = run_dir / "results.json"
    manifest_path = run_dir / "manifest.json"
    if not results_path.exists() or not manifest_path.exists():
        print(f"error: {run_dir} is not a run directory", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = {t.id: t for t in load_tasks(args.tasks or ["tasks"])}
    results = [
        TrialResult.from_dict(dict(r))
        for r in json.loads(results_path.read_text(encoding="utf-8"))
    ]

    print(
        "re-grading needs the trial sandboxes; pass --keep-sandboxes when running",
        file=sys.stderr,
    )
    missing = [r.task_id for r in results if r.task_id not in tasks]
    if missing:
        print(f"error: tasks not found for {sorted(set(missing))}", file=sys.stderr)
        return 2

    report = analyze(
        results,
        model=manifest.get("model", ""),
        baseline=manifest.get("baseline", "baseline"),
    )
    print(render_markdown(report))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    traces = run_dir / "traces.jsonl"
    if not traces.exists():
        print(f"error: no traces.jsonl in {run_dir}", file=sys.stderr)
        return 2
    shown = 0
    for line in traces.read_text(encoding="utf-8").splitlines():
        r = TrialResult.from_dict(json.loads(line))
        if args.task and r.task_id != args.task:
            continue
        if args.harness and r.harness != args.harness:
            continue
        if args.failed_only and r.passed:
            continue
        print(summarize_trace(r))
        print()
        shown += 1
        if args.limit and shown >= args.limit:
            break
    return 0


def _load_run(run_path: Path) -> tuple[list[TrialResult], dict[str, Any]]:
    """Read a run directory, rebuilding results from traces if necessary.

    A run interrupted before it wrote results.json still has traces.jsonl —
    one line per completed trial, appended as it ran. Rebuilding from it means
    an interrupted run is analysable instead of being lost work, and it is the
    same data source --resume uses, so the two cannot disagree.
    """
    run_path = Path(run_path)
    manifest_path = run_path / "manifest.json"
    results_path = run_path / "results.json"
    trace_path = run_path / "traces.jsonl"

    if not manifest_path.exists():
        raise SpecError(f"{run_path} is not a run directory (no manifest.json)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if results_path.exists():
        rows = [
            TrialResult.from_dict(dict(r))
            for r in json.loads(results_path.read_text(encoding="utf-8"))
        ]
        return rows, manifest

    if not trace_path.exists():
        raise SpecError(f"{run_path} has no results.json or traces.jsonl")

    rows = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(TrialResult.from_dict(json.loads(line)))
    return rows, manifest


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare several completed runs side by side.

    Each run is a different model against the same matrix. The interesting
    output is not which model scored highest — it is which model's score is
    most dependent on the harness.
    """
    reports = []
    for run_path in args.runs:
        run_dir = Path(run_path)
        try:
            results, manifest = _load_run(run_dir)
        except (SpecError, json.JSONDecodeError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        reports.append(
            analyze(
                results,
                model=manifest.get("model", str(run_dir)),
                baseline=manifest.get("baseline", "baseline"),
                k=args.k,
            )
        )

    if not reports:
        print("error: no runs to compare", file=sys.stderr)
        return 2

    print("# Cross-model harness sensitivity")
    print()
    print("Same matrix, same tasks, different models. A high HSI means the")
    print("model's measured score is mostly a property of its harness.")
    print()
    print(
        "| model | tasks | trials | pass rate | HSI | max degradation | "
        "largest drop under | largest gain under |"
    )
    print("|---|---:|---:|---:|---:|---:|---|---|")
    for r in sorted(reports, key=lambda x: -x.hsi):
        print(
            f"| `{r.model}` | {r.task_count} | {r.trials_per_cell} | "
            f"{r.overall_pass_rate:.1%} | {r.hsi:.1%} | "
            f"{r.max_degradation:+.1%} | "
            f"`{r.hsi_worst or '-'}` | `{r.hsi_best or '-'}` |"
        )

    # Per-harness deltas, so the shapes can be compared, not just the totals.
    harnesses = sorted({row.harness for r in reports for row in r.rows})
    if harnesses:
        print()
        print("## Delta vs baseline, by harness")
        print()
        header = "| harness | " + " | ".join(f"`{r.model}`" for r in reports) + " |"
        print(header)
        print("|---|" + "---:|" * len(reports))
        for h in harnesses:
            cells = []
            for r in reports:
                row = next((x for x in r.rows if x.harness == h), None)
                cells.append("-" if row is None else f"{row.delta:+.0%}")
            print(f"| `{h}` | " + " | ".join(cells) + " |")

    if any(r.warnings for r in reports):
        print()
        print("## Caveats")
        print()
        for r in reports:
            for w in r.warnings:
                print(f"- `{r.model}`: {w}")

    # Runs that do not cover the same tasks are not comparable, and the table
    # above would imply they are. Compare the per-task coverage, not the
    # aggregate, because that is what a reader would otherwise assume matched.
    coverage = {
        r.model: {task for row in r.rows for task in row.per_task} for r in reports
    }
    if len({frozenset(v) for v in coverage.values()}) > 1:
        print()
        print("**Warning: these runs do not cover the same tasks.** The table")
        print("above compares aggregates across different task sets, which is not")
        print("a valid comparison. Re-run the missing tasks with `--resume` before")
        print("drawing any conclusion.")
        for model, tasks in sorted(coverage.items()):
            print(f"- `{model}`: {len(tasks)} task(s)")
    return 0


def cmd_verify_tasks(args: argparse.Namespace) -> int:
    """Check that every task's starting state still fails and no reference
    solution has leaked into a workspace."""
    from .purity import verify_all

    target = args.tasks[0] if args.tasks else "tasks"
    return verify_all(target)


def cmd_check_solutions(args: argparse.Namespace) -> int:
    from .purity import check_solutions

    target = args.tasks[0] if args.tasks else "tasks"
    return check_solutions(target)


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .purity import calibrate

    target = args.tasks[0] if args.tasks else "tasks"
    return calibrate(target)


def cmd_validate(args: argparse.Namespace) -> int:
    ok = True
    try:
        tasks = load_tasks(args.tasks)
    except SpecError as exc:
        print(f"FAIL tasks: {exc}")
        return 1
    for task in tasks:
        problems = []
        if not task.workspace_path.exists():
            problems.append(f"workspace missing: {task.workspace_path}")
        g = task.grader
        if g.type == "pytest":
            p = (task.root / g.target) if g.target else (task.root / "tests")
            if not p.exists():
                problems.append(f"test dir missing: {p}")
        elif g.type in ("exact", "regex", "diff", "json_assert"):
            if not g.options.get("file"):
                problems.append(f"{g.type} grader needs options.file")
        elif g.type == "exit_code":
            if not g.options.get("command"):
                problems.append("exit_code grader needs options.command")
        if problems:
            ok = False
            for p in problems:
                print(f"FAIL {task.id}: {p}")
        else:
            print(f"ok   {task.id}  (grader={g.type}, steps={task.max_steps})")

    if args.matrix:
        try:
            harnesses, baseline = load_harnesses(args.matrix)
            print(f"ok   matrix: {len(harnesses)} harnesses, baseline={baseline}")
        except (SpecError, json.JSONDecodeError) as exc:
            ok = False
            print(f"FAIL matrix: {exc}")
    return 0 if ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.path)
    if dest.exists() and any(dest.iterdir()):
        print(f"error: {dest} is not empty", file=sys.stderr)
        return 2
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "tests").mkdir(parents=True, exist_ok=True)
    task = {
        "id": dest.name,
        "title": "One-line description of the task",
        "prompt": "Describe exactly what the agent must achieve. Name the file "
        "to change and the behaviour to produce.",
        "max_steps": 15,
        "grader": {"type": "pytest", "target": "tests", "timeout": 120},
        "tags": ["example"],
    }
    (dest / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    (dest / "tests" / "test_example.py").write_text(
        "def test_placeholder():\n    assert True, 'replace this with a real check'\n",
        encoding="utf-8",
    )
    (dest / "workspace" / "README.md").write_text(
        "# Workspace\n\nThe agent sees exactly these files. Keep the starting "
        "state minimal and the failure mode obvious.\n",
        encoding="utf-8",
    )
    print(f"created task scaffold at {dest}")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harnessprobe",
        description="Measure how much a model's measured quality depends on its harness.",
    )
    p.add_argument("--version", action="version", version=f"harnessprobe {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a task x harness matrix")
    run.add_argument("tasks", nargs="+", help="task directories or a parent directory")
    run.add_argument("--matrix", required=True, help="harness matrix JSON file")
    run.add_argument(
        "--provider", default="mock", help="mock | anthropic | openai | deepseek | ..."
    )
    run.add_argument("--model", default="mock-1")
    run.add_argument(
        "--trials", type=int, default=1, help="trials per (task, harness) cell"
    )
    run.add_argument("--out", default=str(DEFAULT_RUNS_DIR))
    run.add_argument("--run-id", default="")
    run.add_argument("--max-tokens", type=int, default=4096)
    run.add_argument("--base-url", default=None)
    run.add_argument(
        "--delay", type=float, default=0.0, help="seconds between provider calls"
    )
    run.add_argument("--keep-sandboxes", action="store_true")
    run.add_argument(
        "--mock-script", default=None, help="trajectory JSON for the mock provider"
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="trials to run in parallel (1 = sequential)",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="skip trials already recorded in this run's traces.jsonl",
    )
    run.add_argument("--notes", default="")
    run.set_defaults(func=cmd_run)

    inspect = sub.add_parser("inspect", help="print saved traces")
    inspect.add_argument("run")
    inspect.add_argument("--task", default="")
    inspect.add_argument("--harness", default="")
    inspect.add_argument("--failed-only", action="store_true")
    inspect.add_argument("--limit", type=int, default=5)
    inspect.set_defaults(func=cmd_inspect)

    redo = sub.add_parser("grade", help="re-analyze a saved run")
    redo.add_argument("run")
    redo.add_argument("tasks", nargs="*")
    redo.set_defaults(func=cmd_grade)

    cmp_ = sub.add_parser("compare", help="compare several runs side by side")
    cmp_.add_argument("runs", nargs="+", help="run directories")
    cmp_.add_argument("--k", type=int, default=1, help="k for flakiness")
    cmp_.set_defaults(func=cmd_compare)

    val = sub.add_parser("validate", help="check tasks and matrix files")
    val.add_argument("tasks", nargs="+")
    val.add_argument("--matrix", default="")
    val.set_defaults(func=cmd_validate)

    verify = sub.add_parser(
        "verify-tasks",
        help="fail if any task starts already-solved or has a leaked solution",
    )
    verify.add_argument("tasks", nargs="*", help="tasks directory (default: tasks)")
    verify.set_defaults(func=cmd_verify_tasks)

    solutions = sub.add_parser(
        "check-solutions",
        help="run every task's reference solution against its own test suite",
    )
    solutions.add_argument("tasks", nargs="*", help="tasks directory (default: tasks)")
    solutions.set_defaults(func=cmd_check_solutions)

    calib = sub.add_parser(
        "calibrate",
        help="grade each reference solution and check the declared difficulty",
    )
    calib.add_argument("tasks", nargs="*", help="tasks directory (default: tasks)")
    calib.set_defaults(func=cmd_calibrate)

    init = sub.add_parser("init", help="scaffold a new task")
    init.add_argument("path")
    init.set_defaults(func=cmd_init)

    return p


def main(argv: list[str] | None = None) -> int:
    # Reports contain non-ASCII in comments only; keep the console path safe on
    # Windows, where stdout defaults to the legacy codepage (GBK, cp1252, ...).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
