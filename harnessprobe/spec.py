"""Core data model for HarnessProbe.

Everything that crosses a process or file boundary is a plain dataclass with
``to_dict`` / ``from_dict`` so traces stay diffable and schema-stable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class SpecError(ValueError):
    """Raised when a task or config file does not satisfy the schema."""


# --------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------

_GRADER_TYPES = {"pytest", "exact", "regex", "diff", "exit_code", "json_assert"}


@dataclass
class GraderSpec:
    type: str
    # pytest / exact / regex / diff / json_assert all use `target`; meaning is
    # grader-specific (test dir, expected text, pattern, expected file, ...).
    target: str = ""
    timeout: int = 120
    # Optional per-grader knobs, passed through untouched.
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in _GRADER_TYPES:
            raise SpecError(
                f"unknown grader type {self.type!r}; expected one of {sorted(_GRADER_TYPES)}"
            )


@dataclass
class Task:
    id: str
    prompt: str
    title: str = ""
    # Directory name (relative to the task dir) copied into the sandbox.
    workspace: str = "workspace"
    grader: GraderSpec = field(default_factory=lambda: GraderSpec(type="exit_code"))
    max_steps: int = 15
    timeout: int = 600
    tags: list[str] = field(default_factory=list)
    difficulty: str = "medium"
    # Files the agent is allowed to touch. Empty = whole workspace.
    readonly: list[str] = field(default_factory=list)
    root: Path | None = None

    @property
    def fingerprint(self) -> str:
        """Stable hash of the parts that change what a run measures."""
        blob = json.dumps(
            {
                "id": self.id,
                "prompt": self.prompt,
                "workspace": self.workspace,
                "grader": asdict(self.grader),
                "max_steps": self.max_steps,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    @classmethod
    def load(cls, task_dir: Path) -> Task:
        task_dir = Path(task_dir).resolve()
        spec_path = task_dir / "task.json"
        if not spec_path.exists():
            raise SpecError(f"missing task.json in {task_dir}")
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
        return cls.from_dict(raw, root=task_dir)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], root: Path | None = None) -> Task:
        if "id" not in raw:
            raise SpecError("task.json requires an 'id'")
        if "prompt" not in raw:
            raise SpecError(f"task {raw.get('id')!r} requires a 'prompt'")
        grader_raw = raw.get("grader", {"type": "exit_code"})
        if isinstance(grader_raw, str):
            grader_raw = {"type": grader_raw}
        return cls(
            id=str(raw["id"]),
            prompt=str(raw["prompt"]),
            title=str(raw.get("title", "")),
            workspace=str(raw.get("workspace", "workspace")),
            grader=GraderSpec(
                type=grader_raw.get("type", "exit_code"),
                target=str(grader_raw.get("target", "")),
                timeout=int(grader_raw.get("timeout", 120)),
                options=dict(grader_raw.get("options", {})),
            ),
            max_steps=int(raw.get("max_steps", 15)),
            timeout=int(raw.get("timeout", 600)),
            tags=list(raw.get("tags", [])),
            difficulty=str(raw.get("difficulty", "medium")),
            readonly=list(raw.get("readonly", [])),
            root=root,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("root", None)
        return d

    @property
    def workspace_path(self) -> Path:
        if self.root is None:
            raise SpecError(f"task {self.id!r} has no root; load it with Task.load()")
        return self.root / self.workspace

    @property
    def grader_path(self) -> Path:
        if self.root is None:
            raise SpecError(f"task {self.id!r} has no root")
        return self.root / self.grader.target if self.grader.target else self.root


# --------------------------------------------------------------------------
# Harness configuration
# --------------------------------------------------------------------------

# The knobs that a real coding agent actually exposes. Each one is something a
# harness author can change without touching the model or the task.
HARNESS_KNOBS = (
    "system_prompt",
    "tool_set",
    "tool_description_style",
    "max_steps",
    "temperature",
    "history_policy",
    "retry_on_error",
    "reasoning_hint",
)

HISTORY_POLICIES = ("full", "drop_old_tool_results", "keep_last_n")
TOOL_DESCRIPTION_STYLES = ("full", "terse", "minimal")


@dataclass
class HarnessConfig:
    """One point in harness space.

    ``name`` is the label used in reports; everything else is a knob. Knobs left
    at ``None`` inherit from the run's baseline, which keeps matrix files short
    and makes the delta attributable to exactly one dimension.
    """

    name: str = "baseline"
    system_prompt: str | None = None
    tool_set: list[str] | None = None
    tool_description_style: str | None = None
    max_steps: int | None = None
    temperature: float | None = None
    history_policy: str | None = None
    retry_on_error: bool | None = None
    reasoning_hint: bool | None = None
    # keep_last_n only
    keep_last_n: int = 6
    # extra free-form metadata that ends up in the report
    notes: str = ""

    def merged(self, base: HarnessConfig) -> HarnessConfig:
        """Return a copy where unset knobs are taken from ``base``."""
        out = HarnessConfig(**asdict(self))
        for knob in HARNESS_KNOBS:
            if getattr(out, knob) is None:
                setattr(out, knob, getattr(base, knob))
        out.name = self.name or base.name
        return out

    def validate(self) -> None:
        if self.history_policy and self.history_policy not in HISTORY_POLICIES:
            raise SpecError(
                f"history_policy {self.history_policy!r} not in {HISTORY_POLICIES}"
            )
        if (
            self.tool_description_style
            and self.tool_description_style not in TOOL_DESCRIPTION_STYLES
        ):
            raise SpecError(
                f"tool_description_style {self.tool_description_style!r} "
                f"not in {TOOL_DESCRIPTION_STYLES}"
            )
        if self.max_steps is not None and self.max_steps < 1:
            raise SpecError("max_steps must be >= 1")
        if self.temperature is not None and not (0.0 <= self.temperature <= 2.0):
            raise SpecError("temperature must be within [0, 2]")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HarnessConfig:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(raw) - known
        if unknown:
            raise SpecError(f"unknown harness knobs: {sorted(unknown)}")
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Trial records
# --------------------------------------------------------------------------


@dataclass
class Step:
    index: int
    kind: str  # "assistant" | "tool_result"
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TrialResult:
    task_id: str
    harness: str
    model: str
    trial: int
    passed: bool
    score: float = 0.0
    steps: list[Step] = field(default_factory=list)
    stop_reason: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    wall_ms: int = 0
    grader_output: str = ""
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TrialResult:
        steps = [Step(**s) for s in raw.pop("steps", [])]
        return cls(steps=steps, **raw)


@dataclass
class RunManifest:
    """Written next to the results so a run can be reproduced exactly."""

    run_id: str
    created_at: str
    provider: str
    model: str
    trials: int
    task_ids: list[str]
    baseline: str
    harnesses: list[str]
    harnessprobe_version: str
    notes: str = ""
    # Hash of the task set, harness knobs, baseline and trial count. A resumed
    # run refuses to reuse trials unless this matches, so a changed experiment
    # cannot silently absorb cached results from a different one.
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
