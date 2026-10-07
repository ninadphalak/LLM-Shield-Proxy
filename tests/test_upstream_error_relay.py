"""A provider's error reason reaches the client, with the proxy's credentials removed.

Before, every upstream 4xx/5xx became `Failed to communicate with upstream provider.`
DeepSeek's "Thinking mode does not support this tool_choice" and Google's "gemini-2.5-flash-lite
is no longer available to new users" were invisible until the same request was sent to the
provider directly (2026-10-06 audit). The old message existed to keep the upstream key out
of the reply, so the relay removes every credential the proxy sent, scrubs the text through
the PII engine one-way, caps it, and keeps the status.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)
UPSTREAM = "https://api.openai.com/v1/chat/completions"
CLIENT_KEY = "sk-proj-mock-key-0123456789abcdef"


def _chat(stream: bool = False):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        json={"model": "gpt-test", "stream": stream, "messages": [{"role": "user", "content": "hello"}]},
    )


@pytest.mark.parametrize("stream", [False, True], ids=["non-stream", "stream"])
def test_the_providers_reason_comes_back_without_the_credential(httpx_mock, stream):
    """OpenAI echoes the key it rejected. The client sees the reason, never the key."""
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        status_code=401,
        json={
            "error": {
                "message": f"Incorrect API key provided: {CLIENT_KEY}. You can find your API key at platform.",
                "type": "invalid_request_error",
                "param": None,
                "code": "invalid_api_key",
            }
        },
    )
    response = _chat(stream=stream)
    body = response.json()
    assert response.status_code == 401
    message = body["error"]["message"]
    assert message.startswith("Upstream provider answered HTTP 401: ")
    assert "Incorrect API key provided: [REDACTED]" in message
    assert CLIENT_KEY not in response.text
    assert body["error"]["type"] == "upstream_error"
    assert body["error"]["code"] == 401
    assert body["error"]["upstream"] == {"type": "invalid_request_error", "code": "invalid_api_key"}


def test_google_list_shaped_error_is_read(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai", raising=False)
    httpx_mock.add_response(
        method="POST",
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        status_code=404, is_reusable=True,
        json=[{"error": {"code": 404, "message": "This model models/gemini-2.5-flash-lite is no longer available to new users.", "status": "NOT_FOUND"}}],
    )
    body = _chat().json()
    assert "no longer available to new users" in body["error"]["message"]
    assert body["error"]["upstream"]["status"] == "NOT_FOUND"
    assert body["error"]["upstream"]["code"] == 404


def test_personal_data_in_the_providers_message_is_scrubbed(httpx_mock):
    """The provider may quote what it received. The request it received was redacted, but
    the relay still scrubs: a value the engine recognises never reaches the client."""
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        status_code=400,
        json={"error": {"message": "Invalid value for user: jane.doe@example.com (card 4111 1111 1111 1111)", "type": "invalid_request_error"}},
    )
    body = _chat().json()
    message = body["error"]["message"]
    assert "jane.doe@example.com" not in message
    assert "4111 1111 1111 1111" not in message
    assert "[REDACTED]" in message


def test_a_non_json_body_is_relayed_as_text_and_capped(httpx_mock):
    httpx_mock.add_response(method="POST", url=UPSTREAM, status_code=502, is_reusable=True, text="<html>Bad gateway " + "x" * 5000 + "</html>")
    body = _chat().json()
    assert body["error"]["message"].startswith("Upstream provider answered HTTP 502: <html>Bad gateway")
    assert body["error"]["message"].endswith("[truncated]")
    assert len(body["error"]["message"]) < 620


def test_an_empty_body_keeps_the_generic_message(httpx_mock):
    httpx_mock.add_response(method="POST", url=UPSTREAM, status_code=503, is_reusable=True, content=b"")
    body = _chat().json()
    assert body["error"]["message"] == "Failed to communicate with upstream provider."
    assert "upstream" not in body["error"]


def test_the_relay_can_be_switched_off(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "RELAY_UPSTREAM_ERROR_MESSAGES", False, raising=False)
    httpx_mock.add_response(method="POST", url=UPSTREAM, status_code=429, is_reusable=True, json={"error": {"message": "Rate limit reached", "type": "rate_limit_error"}})
    body = _chat().json()
    assert body["error"] == {"message": "Failed to communicate with upstream provider.", "type": "upstream_error", "code": 429}


def test_non_chat_routes_relay_too(httpx_mock):
    """`GET /v1/models` and the other pass-through routes used the same generic message."""
    httpx_mock.add_response(
        method="GET",
        url="https://api.openai.com/v1/models",
        status_code=401,
        json={"error": {"message": f"Incorrect API key provided: {CLIENT_KEY}.", "type": "invalid_request_error", "code": "invalid_api_key"}},
    )
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {CLIENT_KEY}"})
    assert response.status_code == 401
    assert "Incorrect API key provided: [REDACTED]" in response.json()["error"]["message"]
    assert CLIENT_KEY not in response.text


def test_a_huge_error_body_is_not_buffered_whole():
    """The cap applies to what is pulled off the wire, not to a body already read in full."""
    import asyncio

    import httpx

    from llm_shield_proxy.api import main as proxy_main

    yielded = 0

    class _Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal yielded
            for _ in range(2048):  # 16 MiB offered
                yielded += 8192
                yield b"x" * 8192

        async def aclose(self):
            return None

    response = httpx.Response(502, stream=_Stream(), request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    raw = asyncio.run(proxy_main._read_error_body(response))
    assert raw is not None and len(raw) == proxy_main._UPSTREAM_ERROR_MAX_BODY_BYTES
    assert yielded <= proxy_main._UPSTREAM_ERROR_MAX_BODY_BYTES + 8192, f"read {yielded} bytes off the wire"


@pytest.mark.parametrize("stream", [False, True], ids=["non-stream", "stream"])
def test_the_scrub_uses_the_credential_that_was_on_the_wire_for_the_kept_body(httpx_mock, monkeypatch, stream):
    """Primary answers 500 echoing its own key; the fallback cannot be reached. The body kept is
    the primary's, so the primary's credential must be the one removed, not the fallback key
    that current_headers carries by the time the reply is built."""
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True, raising=False)
    monkeypatch.setattr(settings, "MAX_RETRIES", 0, raising=False)
    monkeypatch.setattr(settings, "FALLBACK_BASE_URL", "https://fallback.example", raising=False)
    monkeypatch.setattr(settings, "FALLBACK_API_KEY", "sk-fallback-key-zzzz", raising=False)
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        status_code=500,
        json={"error": {"message": f"Server error while handling key {CLIENT_KEY}", "type": "server_error"}},
    )
    httpx_mock.add_exception(httpx.ConnectError("refused"), method="POST", url="https://fallback.example/v1/chat/completions")
    response = _chat(stream=stream)
    assert response.status_code == 500
    assert CLIENT_KEY not in response.text, response.text
    assert "Server error while handling key [REDACTED]" in response.json()["error"]["message"]


def test_a_compressed_error_body_on_the_streaming_path_is_decoded():
    """Providers and CDNs gzip error bodies; the bounded read must decode them, or the client
    gets replacement-character noise in place of the reason."""
    import asyncio
    import gzip
    import json

    import httpx

    from llm_shield_proxy.api import main as proxy_main

    payload = gzip.compress(json.dumps({"error": {"message": "Rate limit reached for gpt-test", "type": "rate_limit_error"}}).encode())

    class _Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload[:10]
            yield payload[10:]

        async def aclose(self):
            return None

    response = httpx.Response(
        429,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        stream=_Stream(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    raw = asyncio.run(proxy_main._read_error_body(response))
    message, details = proxy_main._provider_error_fields(raw)
    assert message == "Rate limit reached for gpt-test"
    assert details == {"type": "rate_limit_error"}
