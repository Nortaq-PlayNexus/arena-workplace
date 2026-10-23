"""Deterministic mock provider for offline tests and demos.

Behaviour is driven by task payload keys (all optional):
  mock_output: str            final text response
  mock_tool_calls: list       [{name, args}] executed in order, then final output
  mock_sleep_s: float         simulate work duration
  mock_failures_remaining: int fail this many times before succeeding
  mock_provider_error: str    always raise: missing_credentials|rate_limit|timeout|api_error
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .base import BaseProvider, ProviderError, ProviderResult, estimate_tokens


class MockProvider(BaseProvider):
    name = "mock"

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             task_context: Optional[dict] = None) -> ProviderResult:
        ctx = task_context or {}
        payload = ctx.get("payload", {}) if isinstance(ctx, dict) else {}
        err = payload.get("mock_provider_error")
        if err == "missing_credentials":
            raise ProviderError("mock: no credentials configured", kind="missing_credentials")
        if err == "rate_limit":
            raise ProviderError("mock: rate limited (429)", kind="rate_limit", retryable=True)
        if err == "timeout":
            raise ProviderError("mock: request timed out", kind="timeout", retryable=True)
        if err == "api_error":
            raise ProviderError("mock: upstream api error (500)", kind="api_error", retryable=True)

        remaining = int(payload.get("mock_failures_remaining", 0) or 0)
        attempt = int(ctx.get("attempt", 0) or 0)
        if attempt < remaining:
            raise ProviderError(f"mock: injected transient failure (attempt {attempt}/{remaining})",
                                kind="api_error", retryable=True)

        sleep_s = float(payload.get("mock_sleep_s", 0) or 0)
        if sleep_s > 0:
            time.sleep(min(sleep_s, 30))

        tool_calls = list(payload.get("mock_tool_calls", []) or [])
        # Only emit tool calls not yet executed (worker tracks executed count in ctx).
        done = int(ctx.get("tools_executed", 0) or 0)
        pending = tool_calls[done:done + 1]
        if pending:
            tc = pending[0]
            content = f"Mock step: calling tool {tc.get('name')}"
            return ProviderResult(content=content, tokens_in=50, tokens_out=20,
                                  tool_calls=[{"name": tc.get("name"), "args": tc.get("args", {})}])
        output = payload.get("mock_output")
        if output is None:
            title = ctx.get("title", "task")
            output = f"Mock completed '{title}': analysed objective and produced a result."
        text_in = " ".join(m.get("content", "") for m in messages)
        return ProviderResult(content=str(output),
                              tokens_in=estimate_tokens(text_in),
                              tokens_out=estimate_tokens(str(output)))
