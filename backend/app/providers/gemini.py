"""Google Gemini API adapter (generateContent)."""
from __future__ import annotations

from typing import Optional

import httpx

from .. import config
from .base import BaseProvider, ProviderError, ProviderResult, estimate_tokens


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, model: str = "", timeout_s: int = 120):
        super().__init__(model=model or "gemini-2.0-flash", timeout_s=timeout_s)

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             task_context: Optional[dict] = None) -> ProviderResult:
        if not config.GEMINI_API_KEY:
            raise ProviderError("GEMINI_API_KEY not configured", kind="missing_credentials")
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        contents = [{"role": "user" if m.get("role") != "assistant" else "model",
                     "parts": [{"text": m.get("content", "")}]}
                    for m in messages if m.get("role") != "system"] or \
                   [{"role": "user", "parts": [{"text": "Begin."}]}]
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools and self.capabilities.get("tool_calling"):
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t.get("description", "")[:300],
                 "parameters": t.get("parameters", {"type": "object"})} for t in tools]}]
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={config.GEMINI_API_KEY}")
        try:
            r = httpx.post(url, json=body, timeout=self.timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderError(f"gemini timeout: {e}", kind="timeout", retryable=True)
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini transport error: {e}", kind="api_error",
                                retryable=True)
        if r.status_code == 429:
            raise ProviderError("gemini rate limited (429)", kind="rate_limit",
                                retryable=True)
        if r.status_code >= 500:
            raise ProviderError(f"gemini server error ({r.status_code})",
                                kind="api_error", retryable=True)
        if r.status_code != 200:
            raise ProviderError(f"gemini error {r.status_code}: {r.text[:300]}",
                                kind="api_error")
        data = r.json()
        content, calls = "", []
        for cand in data.get("candidates", [])[:1]:
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    content += part["text"]
                if "functionCall" in part:
                    fc = part["functionCall"]
                    calls.append({"name": fc.get("name"), "args": fc.get("args", {})})
        meta = data.get("usageMetadata", {})
        return ProviderResult(content=content,
                              tokens_in=int(meta.get("promptTokenCount") or 0),
                              tokens_out=int(meta.get("candidatesTokenCount") or 0),
                              tool_calls=calls)
