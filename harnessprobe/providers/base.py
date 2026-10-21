"""Provider abstraction.

The harness must be identical across models or the comparison means nothing.
So the internal message format is normalised here and each adapter is
responsible only for translation, not for behaviour.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Internal message shapes (dicts, not SDK types, so nothing leaks between
# adapters):
#
#   {"role": "user",      "content": str}
#   {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
#   {"role": "tool",      "tool_call_id": str, "name": str, "content": str}
#
# Adapters translate from this. Nothing else in the codebase knows which
# vendor is being called.

Message = dict[str, Any]


class ProviderError(RuntimeError):
    """A provider call failed in a way the harness cannot recover from."""


class Provider:
    name = "base"
    supports_tools = True

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        auth_token: str | None = None,
        base_url: str | None = None,
        max_retries: int = 4,
        timeout: float = 300.0,
        request_delay: float = 0.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.auth_token = auth_token
        self.base_url = base_url
        self.max_retries = max_retries
        self.timeout = timeout
        self.request_delay = request_delay

    # -- API ---------------------------------------------------------------

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def _sleep(self, attempt: int) -> None:
        delay = min(2.0 ** attempt, 30.0)
        time.sleep(delay)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model!r}>"


def normalize_text(blocks: list[str]) -> str:
    return "\n".join(b for b in blocks if b).strip()
