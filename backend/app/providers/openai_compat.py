"""OpenAI + OpenAI-compatible endpoints (OpenAI, Ollama, LM Studio, vLLM...)."""
from __future__ import annotations

from typing import Optional

import httpx

from .. import config
from .base import BaseProvider, ProviderError, ProviderResult, estimate_tokens


class OpenAICompatProvider(BaseProvider):
    name = "openai_compat"

    def __init__(self, model: str = "", timeout_s: int = 120, base_url: str = ""):
        super().__init__(model=model or "gpt-4o-mini", timeout_s=timeout_s)
        self.base_url = (base_url or config.OPENAI_BASE_URL).rstrip("/")

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             task_context: Optional[dict] = None) -> ProviderResult:
        key = config.OPENAI_API_KEY
        # Local servers often accept empty keys; only OpenAI cloud requires one.
        if "api.openai.com" in self.base_url and not key:
            raise ProviderError("OPENAI_API_KEY not configured", kind="missing_credentials")
        body: dict = {"model": self.model, "messages": messages}
        if tools and self.capabilities.get("tool_calling"):
            body["tools"] = [{"type": "function",
                              "function": {"name": t["name"],
                                           "description": t.get("description", ""),
                                           "parameters": t.get("parameters", {"type": "object"})}}
                             for t in tools]
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}"} if key else {},
                           timeout=self.timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderError(f"openai_compat timeout: {e}", kind="timeout", retryable=True)
        except httpx.HTTPError as e:
            raise ProviderError(f"openai_compat transport error: {e}", kind="api_error",
                                retryable=True)
        if r.status_code == 429:
            raise ProviderError("openai_compat rate limited (429)", kind="rate_limit",
                                retryable=True)
        if r.status_code >= 500:
            raise ProviderError(f"openai_compat server error ({r.status_code})",
                                kind="api_error", retryable=True)
        if r.status_code != 200:
            raise ProviderError(f"openai_compat error {r.status_code}: {r.text[:300]}",
                                kind="api_error")
        data = r.json()
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise ProviderError(f"openai_compat bad response: {str(data)[:300]}",
                                kind="api_error")
        calls = []
        for tc in msg.get("tool_calls", []) or []:
            import json as _j
            try:
                args = _j.loads(tc["function"].get("arguments", "{}"))
            except Exception:
                args = {}
            calls.append({"name": tc["function"]["name"], "args": args})
        usage = data.get("usage", {})
        return ProviderResult(
            content=msg.get("content", "") or "",
            tokens_in=int(usage.get("prompt_tokens") or estimate_tokens(str(messages))),
            tokens_out=int(usage.get("completion_tokens") or estimate_tokens(msg.get("content", ""))),
            tool_calls=calls, raw={"id": data.get("id", "")})
