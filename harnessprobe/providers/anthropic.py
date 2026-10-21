"""Anthropic Messages API adapter.

Speaks the wire protocol directly over ``urllib`` rather than going through the
official SDK. Two reasons, both learned the hard way against real gateways:

1. The SDK injects its own transport (httpx2) which honours system proxy
   settings even for a ``127.0.0.1`` base URL — the request then reaches a
   proxy that answers 502. A local gateway must be called directly.
2. Gateways that proxy the Messages API frequently accept ``Authorization:
   Bearer`` but reject ``x-api-key``. The SDK picks one; here the choice is
   explicit and configurable.

The wire format is stable and small, so owning it costs little and removes a
whole class of environment-specific failures. The official SDK remains a
supported path via ``use_sdk=True`` for first-party Anthropic.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .base import LLMResponse, Message, Provider, ProviderError, ToolCall

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        *,
        use_sdk: bool = False,
        **kw: Any,
    ) -> None:
        super().__init__(model, **kw)
        self.base_url = (self.base_url or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.auth_token = self.auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not (self.auth_token or self.api_key):
            raise ProviderError(
                "set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN (or pass api_key/auth_token)"
            )
        self._use_sdk = use_sdk
        self._sdk_client = None
        if use_sdk:
            self._sdk_client = self._build_sdk_client()

    def _build_sdk_client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - env problem
            raise ProviderError("pip install anthropic") from exc
        kw: dict[str, Any] = {"max_retries": 0, "timeout": self.timeout}
        if self.auth_token:
            kw["auth_token"] = self.auth_token
        else:
            kw["api_key"] = self.api_key
        if self.base_url:
            kw["base_url"] = self.base_url
        return anthropic.Anthropic(**kw)

    # -- request building --------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            # identity: gateways that sit in front of a compressed body are
            # more likely to mangle it, and these responses are small.
            "accept-encoding": "identity",
        }
        if self.auth_token:
            headers["authorization"] = f"Bearer {self.auth_token}"
        else:
            headers["x-api-key"] = self.api_key or ""
        return headers

    def _body(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _to_anthropic(messages),
        }
        if tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                }
                for t in tools
            ]
        # Current-generation models reject sampling params outright, so only
        # send one when it would actually change behaviour.
        if temperature and temperature > 0:
            body["temperature"] = min(temperature, 1.0)
        return body

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
        body = self._body(system, messages, tools, temperature, max_tokens)
        if self._sdk_client is not None:
            return self._complete_via_sdk(body)
        return self._complete_via_http(body)

    def _complete_via_http(self, body: dict[str, Any]) -> LLMResponse:
        url = f"{self.base_url}/v1/messages"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last: str = ""
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=payload, headers=self._headers(), method="POST")
            try:
                # No proxy handler: a gateway on loopback must be reached
                # directly, and urllib would otherwise consult the system
                # proxy settings.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=self.timeout) as fh:
                    data = json.loads(fh.read().decode("utf-8"))
                return _from_anthropic(data, self.model)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last = f"HTTP {exc.code}: {detail}"
                if exc.code == 429 or exc.code >= 500:
                    self._sleep(attempt)
                    continue
                raise ProviderError(f"anthropic {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                self._sleep(attempt)
        raise ProviderError(f"anthropic call failed after {self.max_retries + 1} attempts: {last}")

    def _complete_via_sdk(self, body: dict[str, Any]) -> LLMResponse:
        sdk = __import__("anthropic")
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._sdk_client.messages.create(**body)
                return _from_sdk(resp, self.model)
            except sdk.RateLimitError as exc:
                last = exc
                self._sleep(attempt)
            except sdk.APIStatusError as exc:
                last = exc
                if exc.status_code < 500:
                    raise ProviderError(f"anthropic {exc.status_code}: {exc.message}") from exc
                self._sleep(attempt)
            except sdk.APIConnectionError as exc:
                last = exc
                self._sleep(attempt)
        raise ProviderError(f"anthropic call failed after retries: {last}")


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------


def _to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls", []):
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                )
            out.append(
                {"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]}
            )
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg["tool_call_id"],
                "content": msg["content"],
            }
            # Parallel tool results must share a single user turn.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _blocks_to_response(content: list[dict[str, Any]], usage: dict[str, Any], stop: str, model: str) -> LLMResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content or []:
        if block.get("type") == "text":
            texts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            calls.append(
                ToolCall(id=block.get("id", ""), name=block.get("name", ""), input=dict(block.get("input") or {}))
            )
    return LLMResponse(
        text="\n".join(texts).strip(),
        tool_calls=calls,
        stop_reason=stop or "end_turn",
        input_tokens=int((usage or {}).get("input_tokens") or 0),
        output_tokens=int((usage or {}).get("output_tokens") or 0),
        model=model,
    )


def _from_anthropic(data: dict[str, Any], model: str) -> LLMResponse:
    return _blocks_to_response(
        data.get("content") or [], data.get("usage") or {}, data.get("stop_reason", ""), model
    )


def _from_sdk(resp: Any, model: str) -> LLMResponse:
    content = []
    for block in resp.content:
        if block.type == "text":
            content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            content.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
            )
    usage = getattr(resp, "usage", None)
    return _blocks_to_response(
        content,
        {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        },
        str(resp.stop_reason or "end_turn"),
        model,
    )
