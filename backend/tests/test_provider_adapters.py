"""Adapter tests against a local OpenAI-protocol stub (no cloud keys needed).

These verify request/response wiring, tool-call parsing, and error mapping.
They do NOT test real cloud providers (no credentials in this environment).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.app.providers.base import ProviderError
from backend.app.providers.openai_compat import OpenAICompatProvider


class StubHandler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode() or "{}")
        assert self.path == "/chat/completions", self.path
        if StubHandler.mode == "rate_limit":
            return self._send(429, {"error": "slow down"})
        if StubHandler.mode == "server_error":
            return self._send(500, {"error": "boom"})
        assert "model" in req and "messages" in req
        if "tools" in req:
            return self._send(200, {
                "id": "stub-1",
                "choices": [{"message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "fs_list",
                                                 "arguments": '{"path": "."}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        return self._send(200, {
            "id": "stub-2",
            "choices": [{"message": {"role": "assistant", "content": "STUB-OK"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3}})


@pytest.fixture()
def stub():
    srv = HTTPServer(("127.0.0.1", 0), StubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_openai_compat_plain_chat(stub):
    StubHandler.mode = "ok"
    p = OpenAICompatProvider(model="stub-model", base_url=stub)
    r = p.chat([{"role": "user", "content": "hi"}])
    assert r.content == "STUB-OK"
    assert (r.tokens_in, r.tokens_out) == (8, 3)


def test_openai_compat_tool_calls_parsed(stub):
    StubHandler.mode = "ok"
    p = OpenAICompatProvider(model="stub-model", base_url=stub)
    r = p.chat([{"role": "user", "content": "list files"}],
               tools=[{"name": "fs_list", "description": "ls",
                       "parameters": {"type": "object"}}])
    assert r.tool_calls == [{"name": "fs_list", "args": {"path": "."}}]


def test_openai_compat_rate_limit_retryable(stub):
    StubHandler.mode = "rate_limit"
    p = OpenAICompatProvider(model="stub-model", base_url=stub)
    with pytest.raises(ProviderError) as e:
        p.chat([{"role": "user", "content": "hi"}])
    assert e.value.kind == "rate_limit" and e.value.retryable


def test_openai_compat_server_error_retryable(stub):
    StubHandler.mode = "server_error"
    p = OpenAICompatProvider(model="stub-model", base_url=stub)
    with pytest.raises(ProviderError) as e:
        p.chat([{"role": "user", "content": "hi"}])
    assert e.value.kind == "api_error" and e.value.retryable


def test_openai_cloud_requires_key():
    from backend.app import config
    old_key, old_url = config.OPENAI_API_KEY, config.OPENAI_BASE_URL
    config.OPENAI_API_KEY = ""
    try:
        p = OpenAICompatProvider(model="gpt-4o-mini",
                                 base_url="https://api.openai.com/v1")
        with pytest.raises(ProviderError) as e:
            p.chat([{"role": "user", "content": "hi"}])
        assert e.value.kind == "missing_credentials"
    finally:
        config.OPENAI_API_KEY, config.OPENAI_BASE_URL = old_key, old_url
