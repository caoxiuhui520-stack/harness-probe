"""Tool implementations and their harness-dependent descriptions.

The *descriptions* are the experiment. A real coding harness differs from a
naive one mostly in how much it tells the model about each tool: when to use
it, what it returns, what it does not do. ``full`` / ``terse`` / ``minimal``
model that spectrum explicitly so the delta is measurable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MAX_OUTPUT_CHARS = 12_000
MAX_FILE_BYTES = 400_000


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------
# Description catalog
# --------------------------------------------------------------------------

_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "read_file": {
        "full": (
            "Read a UTF-8 text file from the workspace and return its contents "
            "with line numbers. Use this before editing any file so you know "
            "what is already there. Paths are relative to the workspace root. "
            "Reads are capped at 400KB; for larger files pass offset/limit."
        ),
        "terse": "Read a file from the workspace.",
        "minimal": "Read file.",
    },
    "write_file": {
        "full": (
            "Write text to a file in the workspace, creating parent directories "
            "as needed. This replaces the entire file — to change part of an "
            "existing file, read it first, then write the full new contents. "
            "Prefer one complete write over many small patches."
        ),
        "terse": "Write a file (full replace).",
        "minimal": "Write file.",
    },
    "list_dir": {
        "full": (
            "List the entries of a directory in the workspace. Returns one entry "
            "per line; directories end with '/'. Use this to orient yourself in "
            "an unfamiliar project before reading files."
        ),
        "terse": "List a directory.",
        "minimal": "List dir.",
    },
    "search": {
        "full": (
            "Search file contents with a regular expression and return matching "
            "lines as 'path:line: text'. Use this to find where a symbol is "
            "defined or used instead of reading every file. Searches text files "
            "only and skips .git and __pycache__."
        ),
        "terse": "Regex search across files.",
        "minimal": "Search.",
    },
    "run_command": {
        "full": (
            "Run a shell command with the workspace as the working directory and "
            "return combined stdout/stderr plus the exit code. Use this to run "
            "tests, linters, or the program itself. Non-zero exit codes are "
            "reported as errors but do not end the session — read the output and "
            "fix the problem. Commands are killed after the timeout."
        ),
        "terse": "Run a shell command.",
        "minimal": "Run cmd.",
    },
    "submit": {
        "full": (
            "Declare the task complete. Call this only after you have verified "
            "your work — for example by running the tests and seeing them pass. "
            "Pass a short summary of what you changed. Calling submit ends the "
            "session; you cannot continue afterwards."
        ),
        "terse": "Finish the task.",
        "minimal": "Done.",
    },
}

_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path."},
            "offset": {"type": "integer", "description": "First line (1-based)."},
            "limit": {"type": "integer", "description": "Max lines to return."},
        },
        "required": ["path"],
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    },
    "list_dir": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory."}},
        "required": [],
    },
    "search": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex."},
            "glob": {"type": "string", "description": "Optional glob filter."},
        },
        "required": ["pattern"],
    },
    "run_command": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command."},
            "timeout": {"type": "integer", "description": "Seconds (default 60)."},
        },
        "required": ["command"],
    },
    "submit": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}

ALL_TOOLS = tuple(_SCHEMAS)

# A deliberately narrow set, to measure whether withholding tools hurts.
TOOL_PRESETS: dict[str, list[str]] = {
    "full": list(ALL_TOOLS),
    "no_search": ["read_file", "write_file", "list_dir", "run_command", "submit"],
    "no_shell": ["read_file", "write_file", "list_dir", "search", "submit"],
    "read_write_only": ["read_file", "write_file", "submit"],
    "minimal": ["read_file", "write_file", "submit"],
}


def build_tool_specs(
    tool_names: list[str], description_style: str = "full"
) -> list[dict[str, Any]]:
    """Render tool definitions for a provider, at the requested verbosity."""
    style = description_style if description_style in ("full", "terse", "minimal") else "full"
    specs = []
    for name in tool_names:
        if name not in _SCHEMAS:
            raise ToolError(f"unknown tool {name!r}; have {list(_SCHEMAS)}")
        specs.append(
            {
                "name": name,
                "description": _DESCRIPTIONS[name][style],
                "input_schema": _SCHEMAS[name],
            }
        )
    return specs


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class Sandbox:
    """A throwaway copy of one task workspace.

    Path traversal is rejected rather than normalised: a harness that silently
    rewrites ``../`` hides a model failure that a real deployment would hit.
    """

    def __init__(self, root: Path, readonly: list[str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.readonly = {Path(p).as_posix() for p in (readonly or [])}

    def resolve(self, rel: str) -> Path:
        if os.path.isabs(rel) or re.match(r"^[A-Za-z]:[\\/]", rel):
            raise ToolError(f"absolute paths are not allowed: {rel!r}")
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"path escapes the workspace: {rel!r}")
        return target

    def _check_writable(self, rel: str) -> None:
        if Path(rel).as_posix() in self.readonly:
            raise ToolError(f"{rel!r} is read-only for this task")

    # -- tools ------------------------------------------------------------

    def read_file(self, path: str, offset: int = 0, limit: int = 0) -> ToolResult:
        p = self.resolve(path)
        if not p.exists():
            return ToolResult(f"error: no such file: {path}", is_error=True)
        if p.is_dir():
            return ToolResult(f"error: {path} is a directory", is_error=True)
        if p.stat().st_size > MAX_FILE_BYTES:
            return ToolResult(
                f"error: {path} is {p.stat().st_size} bytes; use offset/limit",
                is_error=True,
            )
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        lines = text.splitlines()
        start = max(offset - 1, 0) if offset else 0
        end = start + limit if limit else len(lines)
        chunk = lines[start:end]
        body = "\n".join(f"{start + i + 1:>5}\t{ln}" for i, ln in enumerate(chunk))
        if not body:
            body = "(empty file)"
        return ToolResult(_truncate(body))

    def write_file(self, path: str, content: str) -> ToolResult:
        # Report a read-only violation the same way as any other tool refusal:
        # as a result the model can read and react to, not an exception that
        # aborts the step.
        try:
            self._check_writable(path)
        except ToolError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        n = len(content.encode("utf-8"))
        return ToolResult(f"wrote {path} ({n} bytes, {content.count(chr(10)) + 1} lines)")

    def list_dir(self, path: str = ".") -> ToolResult:
        p = self.resolve(path)
        if not p.is_dir():
            return ToolResult(f"error: {path} is not a directory", is_error=True)
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        out = [e.name + ("/" if e.is_dir() else "") for e in entries]
        return ToolResult("\n".join(out) if out else "(empty directory)")

    def search(self, pattern: str, glob: str = "") -> ToolResult:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult(f"error: bad regex: {exc}", is_error=True)
        hits: list[str] = []
        for path in sorted(self.root.rglob(glob or "*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(".git/") or "__pycache__" in rel:
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return ToolResult(_truncate("\n".join(hits)))
        return ToolResult("\n".join(hits) if hits else "(no matches)")

    def run_command(self, command: str, timeout: int = 60) -> ToolResult:
        timeout = max(1, min(int(timeout or 60), 600))
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.root),
                capture_output=True,
                timeout=timeout,
                env=env,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(f"error: command timed out after {timeout}s", is_error=True)
        except OSError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        parts = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            parts.append("[stderr]\n" + proc.stderr)
        parts.append(f"[exit {proc.returncode}]")
        return ToolResult(_truncate("\n".join(parts)), is_error=proc.returncode != 0)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    keep = limit - 200
    return text[:keep] + f"\n... [truncated {len(text) - keep} chars]"


Executor = Callable[..., ToolResult]


def make_executor(sandbox: Sandbox) -> dict[str, Executor]:
    return {
        "read_file": sandbox.read_file,
        "write_file": sandbox.write_file,
        "list_dir": sandbox.list_dir,
        "search": sandbox.search,
        "run_command": sandbox.run_command,
        "submit": lambda summary="": ToolResult("submitted"),
    }


def copy_workspace(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        shutil.copytree(src, dst)
    else:
        dst.mkdir(parents=True)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # tiny smoke check
    specs = build_tool_specs(list(ALL_TOOLS), "terse")
    print(_json([s["name"] for s in specs]), file=sys.stderr)
