"""OpenAI-compatible /chat/completions adapter.

One adapter covers OpenAI, DeepSeek, Moonshot, Qwen, OpenRouter, vLLM,
LM Studio and Ollama's OpenAI endpoint — which is the point: a harness result
is only interesting if it holds across vendors.

Stdlib only (urllib), so a fresh clone runs with no extra installs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import LLMResponse, Message, Provider, ProviderError, ToolCall

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}


class OpenAICompatProvider(Provider):
    name = "openai-compat"

    def __init__(
        self,
        model: str,
        *,
        vendor: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(model, api_key=api_key, base_url=base_url, **kw)
        self.vendor = vendor
        self.base_url = (
            base_url
            or os.environ.get(f"{vendor.upper()}_BASE_URL")
            or DEFAULT_BASE_URLS.get(vendor, DEFAULT_BASE_URLS["openai"])
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get(f"{vendor.upper()}_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + _to_openai(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"

        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                    data = json.loads(fh.read().decode("utf-8"))
                return _from_openai(data, self.model)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                last = exc
                if exc.code in (429,) or exc.code >= 500:
                    self._sleep(attempt)
                    continue
                raise ProviderError(f"{self.vendor} HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                self._sleep(attempt)
        raise ProviderError(f"{self.vendor} call failed after retries: {last}")


def _to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or None}
            calls = msg.get("tool_calls", [])
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.input, ensure_ascii=False),
                        },
                    }
                    for c in calls
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
    return out


def _from_openai(data: dict[str, Any], model: str) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls: list[ToolCall] = []
    for i, raw in enumerate(message.get("tool_calls") or []):
        fn = raw.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            # Truncated or malformed arguments are a real model failure mode;
            # surface them as an empty call rather than crashing the trial.
            args = {"__parse_error__": fn.get("arguments")}
        calls.append(ToolCall(id=raw.get("id") or f"call_{i}", name=fn.get("name", ""), input=args))
    usage = data.get("usage") or {}
    return LLMResponse(
        text=(message.get("content") or "").strip(),
        tool_calls=calls,
        stop_reason=str(choice.get("finish_reason") or "end_turn"),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        model=model,
    )
