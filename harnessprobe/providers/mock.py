"""Deterministic mock provider.

Its job is to make the whole pipeline testable offline: CI, contributors
without API keys, and anyone reading the code who wants to see what a trace
looks like before spending money.

The mock is *not* trying to be smart. It is trying to be predictable: it walks
a fixed script derived from the task prompt, so a grader's pass/fail is a
property of the harness and the task, not of the mock.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import LLMResponse, Message, Provider, ToolCall


class MockProvider(Provider):
    name = "mock"

    def __init__(self, model: str = "mock-1", *, script: dict[str, list[dict]] | None = None, **kw: Any) -> None:
        super().__init__(model, **kw)
        self._script = script or {}
        self._calls = 0

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._calls += 1
        available = {t["name"] for t in tools}
        task_key = _task_key(messages)
        plan = self._script.get(task_key) or _default_plan(available)

        step = sum(1 for m in messages if m["role"] == "assistant")
        if step >= len(plan):
            if "submit" in available:
                return _call("submit", {"summary": "mock: plan exhausted"}, self._calls)
            return LLMResponse(text="mock: plan exhausted", stop_reason="end_turn")

        action = plan[step]
        name = action["tool"]
        if name not in available:
            return _call("submit", {"summary": f"mock: tool {name} unavailable"}, self._calls)
        return _call(name, action.get("input", {}), self._calls)


def _call(name: str, args: dict[str, Any], n: int) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=f"mock_{n}", name=name, input=args)],
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=20,
        model="mock-1",
    )


def _task_key(messages: list[Message]) -> str:
    for msg in messages:
        if msg["role"] == "user":
            m = re.search(r"\[task:([\w.-]+)\]", msg["content"])
            if m:
                return m.group(1)
    return ""


def _default_plan(available: set[str]) -> list[dict[str, Any]]:
    """A boring but valid trajectory: look around, read, write, run, submit."""
    plan: list[dict[str, Any]] = []
    if "list_dir" in available:
        plan.append({"tool": "list_dir", "input": {"path": "."}})
    if "run_command" in available:
        plan.append({"tool": "run_command", "input": {"command": "echo mock", "timeout": 10}})
    plan.append({"tool": "submit", "input": {"summary": "mock run"}})
    return plan


def load_script(path: str) -> dict[str, list[dict]]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
