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
    "07-multi-file-bug": {
        "cache.py": '''"""In-memory cache with a TTL.

The eviction policy is documented in docs/design.md — read it, it is the
specification for how expiry is supposed to work.
"""

import time


class Cache:
    def __init__(self, default_ttl=60.0):
        self.default_ttl = default_ttl
        self._store = {}

    def set(self, key, value, ttl=None):
        self._store[key] = (value, time.time() + (ttl or self.default_ttl))

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def _live(self):
        """Drop expired entries, then return the store."""
        now = time.time()
        for key in [k for k, (_, exp) in self._store.items() if now > exp]:
            del self._store[key]
        return self._store

    def size(self):
        return len(self._live())

    def keys(self):
        return list(self._live())
''',
        "session.py": '''"""A session that caches fetched records."""

import time

from cache import Cache


class Session:
    def __init__(self, fetcher, ttl=60.0):
        self.cache = Cache(default_ttl=ttl)
        self._fetcher = fetcher

    def fetch(self, key):
        """Return the cached value, or fetch and cache it."""
        value = self.cache.get(key)
        if value is not None:
            return value
        value = self._fetcher(key)
        self.cache.set(key, value)
        return value

    def reload(self):
        """Re-read every live key. Expired keys are dropped, not refreshed."""
        refreshed = {}
        for key in self.cache.keys():  # keys() sweeps, so this is live-only
            value = self.cache.get(key)
            if value is None:
                continue
            refreshed[key] = self._fetcher(key)
            self.cache.set(key, refreshed[key])
        return refreshed

    def cached_count(self):
        return self.cache.size()
''',
    },
    "08-constraint-refactor": {
        "validators.py": '''"""Input validation for the signup flow."""


def _is_str(value):
    """Shared type guard: every validator opens with this check."""
    return isinstance(value, str)


def validate_email(value):
    if not _is_str(value):
        return False
    if "@" not in value:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def validate_username(value):
    if not _is_str(value):
        return False
    if len(value) < 3 or len(value) > 20:
        return False
    for ch in value:
        if not (ch.isalnum() or ch == "_"):
            return False
    return True


def validate_password(value):
    if not _is_str(value):
        return False
    if len(value) < 8:
        return False
    return True


def validate_age(value):
    if not isinstance(value, int):
        return False
    return 13 <= value <= 130
''',
    },
    "09-long-context": {
        "changelog.py": '''"""Release-note assembly.

A release is built from merged change entries. The rules for what counts as
"user-visible" are in docs/policy.md, and they are the part people get wrong.
"""

import re

_ENTRY = re.compile(r"^- (?P<kind>\\w+): (?P<text>.+)$", re.MULTILINE)


def parse_entries(text):
    """Return the change entries in a changelog fragment, in file order."""
    return [
        {"kind": m.group("kind"), "text": m.group("text")}
        for m in _ENTRY.finditer(text)
    ]


def collect_entries(paths):
    """Read every fragment and concatenate its entries in path order."""
    entries = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            entries.extend(parse_entries(fh.read()))
    return entries


def is_user_visible(entry):
    """Decide whether an entry belongs in the release note.

    Rules, per docs/policy.md (evaluated in this order):
      4. text prefixed `chore:`  -> never visible, overrides everything
      3. kind is Internal        -> never visible
      2. text prefixed `internal:` -> never visible, overrides rule 1
      1. kind is Added or Fixed  -> visible
      5. otherwise               -> visible
    """
    kind = entry["kind"]
    text = entry.get("text", "")

    if text.startswith("chore:"):  # rule 4
        return False
    if kind == "Internal":  # rule 3
        return False
    if text.startswith("internal:"):  # rule 2
        return False
    if kind in ("Added", "Fixed"):  # rule 1
        return True
    return True  # rule 5


def render_release(entries, version):
    """Render a release note: only user-visible entries, grouped by kind."""
    lines = [f"## {version}", ""]
    for kind in ("Added", "Changed", "Fixed"):
        visible = [e for e in entries if e["kind"] == kind and is_user_visible(e)]
        if visible:
            lines.append(f"### {kind}")
            lines.append("")
            lines.extend(f"- {e['text']}" for e in visible)
            lines.append("")
    return "\\n".join(lines).rstrip() + "\\n"
''',
    },
    "10-error-recovery": {
        "fetcher.py": '''"""A small HTTP-ish client with retry, for the sync job."""

import time


class FetchError(Exception):
    """Raised when a fetch ultimately fails."""


class TransientError(FetchError):
    """Worth retrying."""


class PermanentError(FetchError):
    """Retrying will not help."""


class Fetcher:
    def __init__(self, transport, max_attempts=3, base_delay=0.01):
        self.transport = transport
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.attempts = 0

    def fetch(self, url):
        """Fetch a URL, retrying transient failures with backoff.

        Raises PermanentError immediately — retrying a permanent failure wastes
        time and can make the outage worse.
        """
        last = None
        for attempt in range(self.max_attempts):
            self.attempts += 1
            try:
                return self.transport(url, attempt)
            except PermanentError:
                raise
            except TransientError as exc:
                last = exc
                # Only sleep when another attempt will actually follow: a
                # sleep before giving up is pure waste.
                if attempt < self.max_attempts - 1:
                    time.sleep(self.base_delay * (2**attempt))
        raise TransientError(
            f"gave up on {url} after {self.max_attempts} attempts: {last}"
        )
''',
    },
    "11-underspecified": {
        "retry_policy.py": '''"""Retry policy for outbound requests.

Deliberately under-specified. The requirements live in the tests, and where
the tests are silent, choose the behaviour a reasonable engineer would expect
and say so in a docstring.
"""

MAX_ATTEMPTS = 5

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def should_retry(attempt, status_code, error=None):
    """Decide whether to retry a request.

    ``attempt`` is 1-based: 1 is the first try.

    Retryable: 429 and 5xx, or any transport-level error (status_code None).
    Not retryable: 2xx, 4xx other than 429, or once MAX_ATTEMPTS is reached.
    """
    if attempt >= MAX_ATTEMPTS:
        return False
    if status_code is None:
        # No response: the request never completed, so it is worth another try.
        return error is None or True
    return status_code in _RETRYABLE_STATUS


def backoff_delay(attempt, base=0.5, cap=30.0):
    """Exponential backoff with full jitter, capped.

    delay = min(cap, base * 2 ** (attempt - 1))

    Chosen over fixed or linear backoff because it is the standard choice for
    transient server errors, and over pure exponential because an uncapped
    curve stalls a sync job. Deterministic (no random jitter) so a test can
    assert on it.
    """
    exponent = max(0, attempt - 1)
    return min(cap, base * (2**exponent))
''',
    },
    "12-dependency-trace": {
        "app/cli.py": '''"""Command-line entry point."""

from app.report import build_report
from app.store import Store


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["report", "list"])
    args = parser.parse_args(argv)

    store = Store()
    if args.command == "list":
        for name in store.names():
            print(name)
        return 0

    report = build_report(store)
    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        "app/util.py": '''"""Shared helpers."""

import hashlib


def checksum(data):
    """A stable, order-independent digest of the data.

    Order-independent: the same multiset of characters yields the same digest
    regardless of arrangement, which is what the store relies on to dedupe.
    Implemented as the XOR of per-character hashes, so rearranging cannot
    change the result while a different multiset does.
    """
    folded = 0
    for ch in data:
        folded ^= int(hashlib.sha256(ch.encode("utf-8")).hexdigest()[:8], 16)
    return folded % 100000
''',
    },
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
