"""A provider's error reason reaches the client, with the proxy's credentials removed.

Before, every upstream 4xx/5xx became `Failed to communicate with upstream provider.`
DeepSeek's "Thinking mode does not support this tool_choice" and Google's "gemini-2.5-flash-lite
is no longer available to new users" were invisible until the same request was sent to the
provider directly (2026-10-06 audit). The old message existed to keep the upstream key out
of the reply, so the relay removes every credential the proxy sent, scrubs the text through
the PII engine one-way, caps it, and keeps the status.
"""

from __future__ import annotations

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
