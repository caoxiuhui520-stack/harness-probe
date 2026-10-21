"""Provider registry."""

from __future__ import annotations

from typing import Any

from .base import LLMResponse, Message, Provider, ProviderError, ToolCall
from .mock import MockProvider, load_script

__all__ = [
    "LLMResponse",
    "Message",
    "Provider",
    "ProviderError",
    "ToolCall",
    "MockProvider",
    "build_provider",
]


def build_provider(provider: str, model: str, **kwargs: Any) -> Provider:
    """Instantiate a provider by name.

    Imports are lazy so a machine without the `anthropic` package can still run
    OpenAI-compatible and mock providers.
    """
    provider = (provider or "").lower()
    script_path = kwargs.pop("mock_script", None)
    if provider in ("mock", "offline", "dryrun"):
        script = load_script(script_path) if script_path else {}
        kwargs.pop("base_url", None)
        kwargs.pop("request_delay", None)
        return MockProvider(model, script=script, **kwargs)
    if provider in ("anthropic", "claude"):
        from .anthropic import AnthropicProvider

        return AnthropicProvider(model, **kwargs)
    if provider in ("openai", "deepseek", "moonshot", "qwen", "openrouter", "ollama", "lmstudio", "vllm", "openai-compat"):
        from .openai_compat import OpenAICompatProvider

        vendor = "openai" if provider == "openai-compat" else provider
        kwargs.setdefault("vendor", vendor)
        return OpenAICompatProvider(model, **kwargs)
    raise ProviderError(
        f"unknown provider {provider!r}; expected mock, anthropic, or one of "
        "openai/deepseek/moonshot/qwen/openrouter/ollama/lmstudio/vllm"
    )
