"""Task purity checks.

A task whose starting workspace already contains the fix measures nothing, and
it does so silently — the run passes, the report looks fine, and the conclusion
is worthless. That happened during development when a reference-solution
harness wrote into a task's own ``workspace/`` directory.

Two checks, both cheap enough to run in CI on every commit:

``check_tasks`` — the untouched workspace fails its own test suite.
``assert_not_solved`` — the workspace does not contain markers from the
reference solutions.

The second check is the one with teeth, because the first cannot be automated
without running pytest against every task.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Substrings that appear only in a *solved* workspace. Each entry names the task
# it guards so a failure message can say which solution leaked.
_SOLVED_MARKERS: dict[str, list[tuple[str, str]]] = {
    "01-fix-off-by-one": [
        ("workspace/paginate.py", "(page - 1) * per_page"),
    ],
    "02-implement-cli-flag": [
        ("workspace/wordfreq.py", "def main("),
    ],
    "03-refactor-duplication": [
        ("workspace/reports.py", "def format_report("),
    ],
    "04-bug-in-parser": [
        ("workspace/envparse.py", "def _split_value("),
    ],
    "05-add-input-validation": [
        ("workspace/signup.py", "_USERNAME = re.compile"),
    ],
    "06-make-tests-pass": [
        ("workspace/app/config.py", "def _coerce("),
        ("workspace/app/client.py", "attempts = 0\n        while True"),
    ],
}

# A reference solution must never be committed into a task's workspace.
_LEAKED_ARTEFACTS = ("_verify_tests", "solution.py", "solve.py")


@dataclass
class PurityReport:
    task_id: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_task(task_dir: Path) -> PurityReport:
    task_dir = Path(task_dir)
    task_id = task_dir.name
    report = PurityReport(task_id=task_id)

    for artefact in _LEAKED_ARTEFACTS:
        if (task_dir / artefact).exists():
            report.problems.append(f"leaked artefact in task dir: {artefact}")

    for rel, marker in _SOLVED_MARKERS.get(task_id, []):
        path = task_dir / rel
        if not path.exists():
            report.problems.append(f"expected file missing: {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            report.problems.append(
                f"{rel} contains reference-solution code ({marker.strip()!r}); "
                "the task would pass before the agent does anything"
            )

    return report


def check_tasks(root: Path) -> list[PurityReport]:
    root = Path(root)
    return [check_task(d) for d in sorted(root.iterdir()) if d.is_dir()]


def _run_pytest(task_dir: Path) -> tuple[bool, str]:
    """Run the task's suite against a copy of its workspace."""
    import shutil
    import tempfile

    from harnessprobe.spec import Task

    task = Task.load(task_dir)
    tests = (task.root / (task.grader.target or "tests")).resolve()
    if not tests.exists():
        return False, "test directory not found"
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        shutil.copytree(task.workspace_path, ws, dirs_exist_ok=True)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(tests),
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
            ],
            cwd=ws,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    # rc 0 == every test passed == the task is NOT pristine.
    return proc.returncode != 0, (proc.stdout or "")[-400:]


def verify_all(root: Path, *, check_failure: bool = True) -> int:
    """Check every task. Returns 0 when all tasks are clean."""
    reports = check_tasks(root)
    ok = True
    for report in reports:
        if report.ok:
            print(f"ok   {report.task_id}")
            continue
        ok = False
        for problem in report.problems:
            print(f"FAIL {report.task_id}: {problem}")

    if check_failure:
        for task_dir in sorted(Path(root).iterdir()):
            if not task_dir.is_dir() or not (task_dir / "task.json").exists():
                continue
            failed, _ = _run_pytest(task_dir)
            if failed:
                print(f"ok   {task_dir.name} (starting state fails its suite)")
            else:
                ok = False
                print(f"FAIL {task_dir.name}: starting workspace already passes")

    return 0 if ok else 1


# --------------------------------------------------------------------------
# Reference solutions
# --------------------------------------------------------------------------

# A task is only a measurement if a correct solution can pass its suite. These
# are the reference solutions, applied to a scratch copy of each workspace and
# run against the task's own tests. Keeping them in the package (rather than a
# throwaway script) means CI runs them on every commit, so a task whose
# reference solution stops working fails loudly instead of quietly measuring
# nothing.
_REFERENCE_SOLUTIONS: dict[str, dict[str, str]] = {
    "01-fix-off-by-one": {
        "paginate.py": '''"""Page a list of records for the web UI."""


def paginate(items, page, per_page):
    """Return the slice of ``items`` belonging to the 1-based ``page``."""
    start = (page - 1) * per_page
    return items[start : start + per_page]


def page_count(items, per_page):
    """Number of pages needed to show every item."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return (len(items) + per_page - 1) // per_page
''',
    },
    "02-implement-cli-flag": {
        "wordfreq.py": '''"""Word-frequency helper used by the search page."""

import argparse
import json
import re
import sys

_WORD = re.compile(r"[a-z']+")


def tokenize(text):
    """Split text into lowercase word tokens."""
    return _WORD.findall(text.lower())


def word_counts(text):
    """Count how many times each word appears in ``text``."""
    counts = {}
    for word in tokenize(text):
        counts[word] = counts.get(word, 0) + 1
    return counts


def top_words(text, n=10):
    """Return the ``n`` most common words as (word, count), highest first."""
    counts = word_counts(text)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        print(f"no such file: {args.file}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(word_counts(text), indent=2))
    else:
        for word, count in top_words(text, args.top):
            print(f"{word} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    },
    "03-refactor-duplication": {
        "reports.py": '''"""Report formatting for the admin dashboard."""


def format_report(rows, title):
    lines = []
    lines.append("=" * 40)
    lines.append(title)
    lines.append("=" * 40)
    for row in rows:
        lines.append(f"{row['name']:<20} {row['value']:>10}")
    lines.append("-" * 40)
    lines.append(f"total: {len(rows)}")
    return "\\n".join(lines)


def format_user_report(rows):
    return format_report(rows, "USER REPORT")


def format_order_report(rows):
    return format_report(rows, "ORDER REPORT")


def format_audit_report(rows):
    return format_report(rows, "AUDIT REPORT")
''',
    },
    "04-bug-in-parser": {
        "envparse.py": '''"""Minimal .env file parser."""


def _split_value(raw):
    """Split a raw value into a value, honouring quotes."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end == -1:
            raise ValueError("unterminated quoted value")
        value = raw[1:end]
        rest = raw[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise ValueError(f"trailing garbage after quoted value: {rest!r}")
        return value
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    return raw.strip()


def parse_line(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        raise ValueError(f"not a KEY=value line: {line!r}")
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"empty key in line: {line!r}")
    return key, _split_value(value)


def parse(text):
    out = {}
    for raw in text.splitlines():
        parsed = parse_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        out[key] = value
    return out
''',
    },
    "05-add-input-validation": {
        "signup.py": '''"""Signup form handling."""

import re

_USERNAME = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_EMAIL = re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def validate_signup(payload):
    errors = {}

    username = str(payload.get("username", "")).strip().lower()
    if not username:
        errors["username"] = "required"
    elif not _USERNAME.match(username):
        errors["username"] = "3-32 chars, letters/digits/_/- only"

    email = str(payload.get("email", "")).strip().lower()
    if not email:
        errors["email"] = "required"
    elif not _EMAIL.match(email):
        errors["email"] = "invalid email"

    raw_age = payload.get("age")
    age = None
    if raw_age is None or raw_age == "":
        errors["age"] = "required"
    else:
        try:
            age = int(raw_age)
        except (TypeError, ValueError):
            errors["age"] = "must be a number"
        else:
            if age < 13:
                errors["age"] = "must be at least 13"
            elif age > 130:
                errors["age"] = "unrealistic"

    if errors:
        raise ValidationError(errors)
    return {"username": username, "email": email, "age": age}
''',
    },
    "06-make-tests-pass": {
        "app/config.py": '''"""Application settings."""

import json
import os

_DEFAULTS = {
    "debug": False,
    "retries": 3,
    "endpoint": "https://api.example.com",
}


def _coerce(raw, default):
    """Cast an environment string to the type of the default it overrides."""
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def load_settings(path=None):
    settings = dict(_DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            settings.update(json.load(fh))
    for key, default in _DEFAULTS.items():
        env_key = "APP_" + key.upper()
        if env_key in os.environ:
            settings[key] = _coerce(os.environ[env_key], default)
    return settings
''',
        "app/client.py": '''"""HTTP client used by the sync job."""

from .config import load_settings


class Client:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else load_settings()
        self.attempts = 0

    @property
    def endpoint(self):
        return self.settings["endpoint"]

    def max_attempts(self):
        return int(self.settings["retries"])

    def fetch(self, path):
        self.attempts += 1
        if self.attempts > self.max_attempts():
            raise RuntimeError(f"gave up after {self.max_attempts()} attempts")
        return {"path": path, "endpoint": self.endpoint, "attempt": self.attempts}

    def reset(self):
        self.attempts = 0
''',
        "app/sync.py": '''"""Nightly sync job."""

from .client import Client
from .config import load_settings


def sync(paths, settings=None):
    settings = settings if settings is not None else load_settings()
    client = Client(settings)
    results = []
    failures = []
    for path in paths:
        client.reset()
        try:
            results.append(client.fetch(path))
        except RuntimeError as exc:
            failures.append({"path": path, "error": str(exc)})
    return {"results": results, "failures": failures, "endpoint": client.endpoint}
''',
    },
}


def check_solutions(root: Path) -> int:
    """Apply each reference solution to a scratch copy and run its suite."""
    import shutil
    import tempfile

    root = Path(root)
    ok = True
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir() or not (task_dir / "task.json").exists():
            continue
        task_id = task_dir.name
        files = _REFERENCE_SOLUTIONS.get(task_id)
        if not files:
            print(f"SKIP {task_id}: no reference solution")
            ok = False
            continue

        from harnessprobe.spec import Task

        task = Task.load(task_dir)
        tests = (task.root / (task.grader.target or "tests")).resolve()
        if not tests.exists():
            print(f"FAIL {task_id}: test directory not found")
            ok = False
            continue

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(task.workspace_path, ws, dirs_exist_ok=True)
            for rel, text in files.items():
                dest = ws / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(tests),
                    "-q",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=ws,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
        if proc.returncode == 0:
            print(f"ok   {task_id} (reference solution passes)")
        else:
            ok = False
            print(f"FAIL {task_id}: reference solution does not pass")
            print((proc.stdout or "")[-1500:])
    return 0 if ok else 1
