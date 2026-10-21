"""Graders.

A grader answers one question: did the agent actually change the world in the
way the task asked for? It runs against the trial's sandbox after the loop
ends, never against the transcript — models claim success they did not achieve,
and a transcript-based grader rewards confident wording.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from ..spec import GraderSpec, Task, TrialResult

GRADER_VERSION = "1"


class GradeResult:
    def __init__(self, passed: bool, score: float = 0.0, output: str = "") -> None:
        self.passed = passed
        self.score = score
        self.output = output


def grade(task: Task, result: TrialResult, sandbox: Path) -> TrialResult:
    spec = task.grader
    try:
        outcome = _DISPATCH[spec.type](task, spec, sandbox)
    except Exception as exc:  # a broken grader is a task bug, not a model bug
        outcome = GradeResult(False, 0.0, f"grader crashed: {type(exc).__name__}: {exc}")
    result.passed = outcome.passed
    result.score = outcome.score
    result.grader_output = outcome.output[:8000]
    return result


# --------------------------------------------------------------------------


def _pytest(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """Run the task's test suite inside the sandbox.

    The suite is read from the task directory, never from the sandbox copy. A
    read-only copy is staged into the sandbox so the agent can run it while
    working, but an agent that rewrites those tests changes nothing here.
    """
    tests = Path(spec.target) if spec.target else Path("tests")
    if not tests.is_absolute():
        tests = (task.root or Path(".")) / tests
    if not tests.exists():
        return GradeResult(False, 0.0, f"test path not found: {tests}")
    tests = tests.resolve()

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(tests),
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            env=env,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return GradeResult(False, 0.0, f"pytest timed out after {spec.timeout}s")
    out = (proc.stdout or "") + (proc.stderr or "")
    passed = proc.returncode == 0
    return GradeResult(passed, 1.0 if passed else 0.0, out)


def _exact(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """Compare a produced file against an expected file, byte for byte."""
    target = _resolve_target(task, spec, sandbox)
    expected = Path(spec.target) if spec.target else task.grader_path
    if not expected.is_absolute():
        expected = (task.root or Path(".")) / spec.options.get("expected", "expected.txt")
    if not target.exists():
        return GradeResult(False, 0.0, f"missing output file: {target}")
    if not expected.exists():
        return GradeResult(False, 0.0, f"missing expected file: {expected}")
    got = target.read_text(encoding="utf-8", errors="replace").strip()
    want = expected.read_text(encoding="utf-8", errors="replace").strip()
    ok = got == want
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"got {got[:400]!r}\nwant {want[:400]!r}")


def _regex(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """Pattern must appear in the produced file. Cheap but blunt."""
    target = _resolve_target(task, spec, sandbox)
    if not target.exists():
        return GradeResult(False, 0.0, f"missing output file: {target}")
    text = target.read_text(encoding="utf-8", errors="replace")
    pattern = spec.options.get("pattern", "")
    if not pattern:
        return GradeResult(False, 0.0, "regex grader needs options.pattern")
    ok = re.search(pattern, text, re.MULTILINE) is not None
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"pattern {pattern!r} not found")


def _diff(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """File must match a golden file, ignoring trailing whitespace."""
    target = _resolve_target(task, spec, sandbox)
    golden = (task.root or Path(".")) / spec.options.get("golden", "golden.txt")
    if not target.exists():
        return GradeResult(False, 0.0, f"missing output file: {target}")
    if not golden.exists():
        return GradeResult(False, 0.0, f"missing golden file: {golden}")
    got = [ln.rstrip() for ln in target.read_text(encoding="utf-8", errors="replace").splitlines()]
    want = [ln.rstrip() for ln in golden.read_text(encoding="utf-8", errors="replace").splitlines()]
    if got == want:
        return GradeResult(True, 1.0, "")
    for i, (a, b) in enumerate(zip(got, want), 1):
        if a != b:
            return GradeResult(False, 0.0, f"first difference at line {i}: got {a!r}, want {b!r}")
    return GradeResult(False, 0.0, f"length differs: got {len(got)} lines, want {len(want)}")


def _exit_code(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """Run a command in the sandbox; exit 0 means pass."""
    command = spec.options.get("command", "")
    if not command:
        return GradeResult(False, 0.0, "exit_code grader needs options.command")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return GradeResult(False, 0.0, f"command timed out after {spec.timeout}s")
    ok = proc.returncode == 0
    out = ((proc.stdout or "") + (proc.stderr or ""))[:4000]
    return GradeResult(ok, 1.0 if ok else 0.0, out)


def _json_assert(task: Task, spec: GraderSpec, sandbox: Path) -> GradeResult:
    """Load a JSON file and check a dotted path against an expected value."""
    target = _resolve_target(task, spec, sandbox)
    if not target.exists():
        return GradeResult(False, 0.0, f"missing output file: {target}")
    try:
        data: Any = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return GradeResult(False, 0.0, f"invalid json: {exc}")
    path = spec.options.get("path", "")
    expected = spec.options.get("equals")
    node = data
    for part in [p for p in path.split(".") if p]:
        if isinstance(node, list):
            try:
                node = node[int(part)]
                continue
            except (ValueError, IndexError):
                return GradeResult(False, 0.0, f"cannot index {part!r} in list")
        if not isinstance(node, dict) or part not in node:
            return GradeResult(False, 0.0, f"missing key {part!r} at {path}")
        node = node[part]
    ok = node == expected
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"at {path}: got {node!r}, want {expected!r}")


def _resolve_target(task: Task, spec: GraderSpec, sandbox: Path) -> Path:
    rel = spec.options.get("file", "")
    if not rel:
        raise ValueError(f"{spec.type} grader needs options.file")
    return (sandbox / rel).resolve()


_DISPATCH: dict[str, Callable[[Task, GraderSpec, Path], GradeResult]] = {
    "pytest": _pytest,
    "exact": _exact,
    "regex": _regex,
    "diff": _diff,
    "exit_code": _exit_code,
    "json_assert": _json_assert,
}
