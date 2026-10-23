"""Anthropic Messages API adapter."""
from __future__ import annotations

from typing import Optional

import httpx

from .. import config
from .base import BaseProvider, ProviderError, ProviderResult, estimate_tokens


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, model: str = "", timeout_s: int = 120):
        super().__init__(model=model or "claude-haiku-4-5", timeout_s=timeout_s)

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             task_context: Optional[dict] = None) -> ProviderResult:
        if not config.ANTHROPIC_API_KEY:
            raise ProviderError("ANTHROPIC_API_KEY not configured", kind="missing_credentials")
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        msgs = [{"role": "user" if m.get("role") != "assistant" else "assistant",
                 "content": m.get("content", "")}
                for m in messages if m.get("role") != "system"] or \
               [{"role": "user", "content": "Begin."}]
        body: dict = {"model": self.model, "max_tokens": 2048, "messages": msgs}
        if system:
            body["system"] = system
        if tools and self.capabilities.get("tool_calling"):
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t.get("parameters", {"type": "object"})}
                             for t in tools]
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", json=body, headers={
                "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                timeout=self.timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderError(f"anthropic timeout: {e}", kind="timeout", retryable=True)
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic transport error: {e}", kind="api_error",
                                retryable=True)
        if r.status_code == 429:
            raise ProviderError("anthropic rate limited (429)", kind="rate_limit",
                                retryable=True)
        if r.status_code >= 500:
            raise ProviderError(f"anthropic server error ({r.status_code})",
                                kind="api_error", retryable=True)
        if r.status_code != 200:
            raise ProviderError(f"anthropic error {r.status_code}: {r.text[:300]}",
                                kind="api_error")
        data = r.json()
        content, calls = "", []
        for b in data.get("content", []):
            if b.get("type") == "text":
                content += b.get("text", "")
            elif b.get("type") == "tool_use":
                calls.append({"name": b.get("name"), "args": b.get("input", {})})
        usage = data.get("usage", {})
        return ProviderResult(content=content,
                              tokens_in=int(usage.get("input_tokens") or 0),
                              tokens_out=int(usage.get("output_tokens") or 0),
                              tool_calls=calls)
