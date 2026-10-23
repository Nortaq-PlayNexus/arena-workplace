"""Provider abstraction layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class ProviderError(Exception):
    def __init__(self, message: str, kind: str = "error", retryable: bool = False):
        super().__init__(message)
        self.kind = kind  # missing_credentials | rate_limit | timeout | api_error
        self.retryable = retryable


@dataclass
class ProviderResult:
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: list[dict] = field(default_factory=list)  # [{name, args}]
    raw: dict = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


class BaseProvider:
    name = "base"

    def __init__(self, model: str = "", timeout_s: int = 120):
        self.model = model
        self.timeout_s = timeout_s

    @property
    def capabilities(self) -> dict:
        from .. import config
        return config.PROVIDER_CAPABILITIES.get(self.name, {})

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             task_context: Optional[dict] = None) -> ProviderResult:
        raise NotImplementedError


def get_provider(name: str, model: str = "", timeout_s: int = 120) -> BaseProvider:
    name = (name or "mock").lower()
    if name == "mock":
        from .mock import MockProvider
        return MockProvider(model=model or "mock-small", timeout_s=timeout_s)
    if name in ("openai_compat", "openai"):
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(model=model, timeout_s=timeout_s)
    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(model=model, timeout_s=timeout_s)
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(model=model, timeout_s=timeout_s)
    raise ProviderError(f"unknown provider: {name}", kind="api_error")
