"""The agent loop.

Every harness knob in ``HarnessConfig`` is applied here and nowhere else. If a
knob does not change behaviour in this file, it does not belong in the config —
a measurement tool that silently ignores a setting is worse than no tool.
"""

from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import LLMResponse, Message, Provider, ProviderError
from .spec import HarnessConfig, Step, Task, TrialResult
from .tools import Sandbox, ToolError, build_tool_specs, copy_workspace, make_executor

# --------------------------------------------------------------------------
# Prompt templates
# --------------------------------------------------------------------------

BASE_SYSTEM = """\
You are a software engineer working inside a sandboxed project workspace.

Rules:
- Use the provided tools to inspect and change files. You cannot see the \
filesystem without them.
- Paths you pass to tools are relative to the workspace root.
- Work until the task is genuinely complete, then call `submit`.
- If a command fails, read the output and fix the cause instead of retrying \
the same command.
"""

REASONING_HINT = """
Before each tool call, state in one sentence what you are about to do and why. \
Do not narrate more than that.
"""

NO_REASONING_HINT = """
Do not narrate your plan. Call tools directly.
"""


def build_system_prompt(cfg: HarnessConfig) -> str:
    parts = [cfg.system_prompt if cfg.system_prompt is not None else BASE_SYSTEM]
    parts.append(REASONING_HINT if cfg.reasoning_hint else NO_REASONING_HINT)
    return "\n".join(p.strip() for p in parts if p and p.strip())


# --------------------------------------------------------------------------
# History policies
# --------------------------------------------------------------------------


def apply_history_policy(messages: list[Message], cfg: HarnessConfig) -> list[Message]:
    """Return the message list the model actually sees.

    This is where most real harnesses quietly differ from each other, and where
    long sessions start to degrade. Keeping it explicit makes it measurable.
    """
    policy = cfg.history_policy or "full"
    if policy == "full":
        return messages

    if policy == "drop_old_tool_results":
        # Keep every assistant turn (so tool_use ids stay paired) but replace
        # old tool results with a stub. The last 4 results stay intact.
        tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
        keep = set(tool_idx[-4:])
        out: list[Message] = []
        for i, msg in enumerate(messages):
            if msg["role"] == "tool" and i not in keep:
                out.append(
                    {**msg, "content": "[older tool output elided to save context]"}
                )
            else:
                out.append(msg)
        return out

    if policy == "keep_last_n":
        # Hard window over *turns*, always keeping the original user message so
        # the model never loses the task statement.
        n = max(1, cfg.keep_last_n)
        if len(messages) <= n + 1:
            return messages
        head = messages[:1]
        tail = messages[-n:]
        # Never start the tail on a tool result with no matching assistant turn.
        while tail and tail[0]["role"] == "tool":
            tail = tail[1:]
        return head + tail

    return messages


# --------------------------------------------------------------------------
# Single trial
# --------------------------------------------------------------------------


@dataclass
class TrialContext:
    task: Task
    cfg: HarnessConfig
    provider: Provider
    sandbox_root: Path
    trial_index: int = 0
    max_tokens: int = 4096


def run_trial(ctx: TrialContext) -> TrialResult:
    task, cfg = ctx.task, ctx.cfg
    sandbox = Sandbox(ctx.sandbox_root, task.readonly)
    executor = make_executor(sandbox)
    tool_names = cfg.tool_set or [
        "read_file",
        "write_file",
        "list_dir",
        "search",
        "run_command",
        "submit",
    ]
    # `submit` is the loop's exit signal, not an optional capability.
    if "submit" not in tool_names:
        tool_names = [*tool_names, "submit"]
    tool_specs = build_tool_specs(tool_names, cfg.tool_description_style or "full")

    system = build_system_prompt(cfg)
    messages: list[Message] = [
        {"role": "user", "content": f"[task:{task.id}]\n\n{task.prompt}"}
    ]
    steps: list[Step] = []
    result = TrialResult(
        task_id=task.id,
        harness=cfg.name,
        model=ctx.provider.model,
        trial=ctx.trial_index,
        passed=False,
    )
    started = time.time()
    submitted = False
    max_steps = cfg.max_steps or task.max_steps

    for step_index in range(max_steps):
        if time.time() - started > task.timeout:
            result.stop_reason = "task_timeout"
            break

        view = apply_history_policy(messages, cfg)
        try:
            resp: LLMResponse = ctx.provider.complete(
                system=system,
                messages=view,
                tools=tool_specs,
                temperature=cfg.temperature or 0.0,
                max_tokens=ctx.max_tokens,
            )
        except ProviderError as exc:
            result.error = f"provider: {exc}"
            result.stop_reason = "provider_error"
            break

        result.input_tokens += resp.input_tokens
        result.output_tokens += resp.output_tokens
        steps.append(
            Step(
                index=step_index,
                kind="assistant",
                text=resp.text,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
            )
        )

        if not resp.tool_calls:
            # Text-only turn. Nudge once, then treat as finished to avoid
            # burning the budget on a model that will not use tools.
            messages.append({"role": "assistant", "content": resp.text})
            if cfg.retry_on_error and step_index < max_steps - 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You did not call a tool. Continue the task by calling "
                            "a tool, or call `submit` if you are done."
                        ),
                    }
                )
                continue
            result.stop_reason = "no_tool_call"
            break

        messages.append(
            {
                "role": "assistant",
                "content": resp.text,
                "tool_calls": resp.tool_calls,
            }
        )

        for call in resp.tool_calls:
            fn = executor.get(call.name)
            if fn is None:
                output, is_error = f"error: unknown tool {call.name!r}", True
            else:
                try:
                    tr = fn(**call.input)
                    output, is_error = tr.content, tr.is_error
                except ToolError as exc:
                    output, is_error = f"error: {exc}", True
                except TypeError as exc:
                    output, is_error = f"error: bad arguments: {exc}", True
                except Exception as exc:  # tool bugs must not kill the trial
                    output, is_error = f"error: {type(exc).__name__}: {exc}", True

            steps.append(
                Step(
                    index=step_index,
                    kind="tool_result",
                    tool_name=call.name,
                    tool_input=call.input,
                    tool_output=output,
                    error="tool_error" if is_error else "",
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": output,
                }
            )

            if call.name == "submit":
                submitted = True

        if submitted:
            result.stop_reason = "submitted"
            break
        if resp.stop_reason == "max_tokens":
            result.stop_reason = "max_tokens"
            break
    else:
        result.stop_reason = "max_steps"

    result.steps = steps
    result.wall_ms = int((time.time() - started) * 1000)
    return result


# --------------------------------------------------------------------------
# Matrix execution
# --------------------------------------------------------------------------


@dataclass
class MatrixJob:
    tasks: list[Task]
    harnesses: list[HarnessConfig]
    provider: Provider
    trials: int = 1
    work_dir: Path = Path(".harnessprobe")
    max_tokens: int = 4096
    keep_sandboxes: bool = False
    # Called after the trial runs but before cleanup, so the grader can see the
    # sandbox. Receives (task, result, sandbox_root) and returns a TrialResult.
    grade: Any = None
    # Trials are independent, so they run in a thread pool. A provider call is
    # almost entirely network wait; the sandbox work is per-thread and touches
    # only that trial's own directory.
    concurrency: int = 4


@dataclass
class MatrixOutcome:
    results: list[TrialResult] = field(default_factory=list)


def _run_one(
    job: MatrixJob, task: Task, cfg: HarnessConfig, trial: int, sandbox_base: Path
) -> TrialResult:
    label = _safe(f"{task.id}__{cfg.name}__t{trial}")
    sandbox_root = sandbox_base / label
    copy_workspace(task.workspace_path, sandbox_root)
    # The tests are visible to the agent so it can verify its work, but they are
    # marked read-only and grading uses the pristine copy from the task
    # directory — editing them changes nothing that is measured.
    _stage_tests(task, sandbox_root)
    try:
        result = run_trial(
            TrialContext(
                task=task,
                cfg=cfg,
                provider=job.provider,
                sandbox_root=sandbox_root,
                trial_index=trial,
                max_tokens=job.max_tokens,
            )
        )
        if job.grade is not None:
            result = job.grade(task, result, sandbox_root)
    finally:
        if not job.keep_sandboxes and sandbox_root.exists():
            _remove_tree(sandbox_root)
    return result


def _remove_tree(root: Path) -> None:
    """Delete a sandbox, including the read-only files staged into it.

    Windows refuses to unlink a read-only file, so `rmtree` leaves them behind
    and the sandbox directory accumulates across runs — which also means the
    staged tests stay readable on disk after the trial is over. Clearing the
    read-only bit before deleting is what makes cleanup actually happen.
    """
    for path in root.rglob("*"):
        try:
            if path.is_file() and not (path.stat().st_mode & 0o200):
                path.chmod(0o600)
        except OSError:
            pass
    shutil.rmtree(root, ignore_errors=True)


def run_matrix(
    job: MatrixJob,
    on_result: Any = None,
    skip: set[tuple[str, str, int]] | None = None,
) -> MatrixOutcome:
    """Run every (task x harness x trial) combination.

    Sandboxes are created fresh per trial: a harness that leaves broken state
    behind must not contaminate the next measurement. Grading happens inside
    the same unit of work, against that trial's sandbox only.

    ``skip`` holds (task_id, harness_name, trial) triples already completed —
    used by the CLI's ``--resume`` so an interrupted run does not re-spend
    tokens. The caller is responsible for merging the skipped results back in.

    Results are returned in completion order. ``on_result`` is called from
    worker threads, so it must be thread-safe.
    """
    out = MatrixOutcome()
    job.work_dir.mkdir(parents=True, exist_ok=True)
    sandbox_base = job.work_dir / "sandboxes"

    units = [
        (task, cfg, trial)
        for task in job.tasks
        for cfg in job.harnesses
        for trial in range(job.trials)
    ]
    if skip:
        units = [u for u in units if (u[0].id, u[1].name, u[2]) not in skip]
    if not units:
        return out
    workers = max(1, min(job.concurrency, len(units)))

    if workers == 1:
        for task, cfg, trial in units:
            result = _run_one(job, task, cfg, trial, sandbox_base)
            out.results.append(result)
            if on_result:
                on_result(result)
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_one, job, task, cfg, trial, sandbox_base)
            for task, cfg, trial in units
        ]
        for future in as_completed(futures):
            result = future.result()
            out.results.append(result)
            if on_result:
                on_result(result)
    return out


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in label)


def _stage_tests(task: Task, sandbox_root: Path) -> None:
    """Copy the task's tests into the sandbox, read-only.

    Without this the agent is told to run the tests and cannot find them, which
    measures the prompt's honesty rather than the model. The copy is read-only
    and grading reads the original, so tampering has no effect on the score.
    """
    if task.grader.type != "pytest" or task.root is None:
        return
    tests_src = task.root / (task.grader.target or "tests")
    if not tests_src.is_dir():
        return
    dest = sandbox_root / "tests"
    if dest.exists():
        return
    try:
        shutil.copytree(tests_src, dest)
    except OSError:
        return
    # Make the staged copy read-only so a write attempt fails loudly instead of
    # appearing to succeed.
    for path in dest.rglob("*"):
        if path.is_file():
            try:
                path.chmod(0o444)
            except OSError:
                pass


def summarize_trace(result: TrialResult, limit: int = 2000) -> str:
    """Compact human-readable trace for reports and debugging."""
    lines = [
        (
            f"task={result.task_id} harness={result.harness} trial={result.trial} "
            f"passed={result.passed} stop={result.stop_reason}"
        ),
    ]
    if result.error:
        lines.append(f"error: {result.error}")
    for step in result.steps:
        if step.kind == "assistant":
            if step.text:
                lines.append(f"  [{step.index}] think: {step.text[:200]}")
        else:
            args = json.dumps(step.tool_input, ensure_ascii=False)[:160]
            lines.append(f"  [{step.index}] {step.tool_name}({args})")
            out = step.tool_output.strip().splitlines()
            if out:
                lines.append(f"        -> {out[0][:160]}")
    text = "\n".join(lines)
    return text[:limit]
