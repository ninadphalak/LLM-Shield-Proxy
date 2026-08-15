"""End-to-end integration tests for LLM-Shield-Proxy streaming and non-streaming workflows."""

import json

from fastapi.testclient import TestClient

from llm_shield_proxy.config import settings
from llm_shield_proxy.main import app

client = TestClient(app)


def test_proxy_chat_completion_with_pii(monkeypatch, httpx_mock):
    """Verifies that a standard chat completion request redacts input PII and rehydrates response."""
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", False)
    test_prompt = "My name is John Doe, and my phone number is 555-0199. What can you tell me about data privacy?"

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello [PERSON_1], I have noted your contact [PHONE_1]."}}
            ],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": test_prompt}]},
    )

    assert response.status_code == 200
    res_data = response.json()
    content = res_data["choices"][0]["message"]["content"]
    assert "Hello John Doe, I have noted your contact 555-0199." == content
    assert "[PERSON_1]" not in content
    assert "[PHONE_1]" not in content


def test_proxy_synthetic_swapping_e2e(monkeypatch, httpx_mock):
    """Verifies end-to-end synthetic swapping with dynamic upstream response."""
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", True)
    test_prompt = "Patient John Doe visited our clinic."

    def response_callback(request):
        req_json = json.loads(request.content.decode("utf-8"))
        sent_content = req_json["messages"][0]["content"]
        # Upstream echoes the synthetic name sent in the request
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl-synth",
                "choices": [{"message": {"role": "assistant", "content": f"Acknowledged {sent_content}"}}],
            },
        )

    import httpx

    httpx_mock.add_callback(response_callback)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": test_prompt}]},
    )

    assert response.status_code == 200
    res_data = response.json()
    content = res_data["choices"][0]["message"]["content"]
    assert "John Doe" in content
    assert "[" not in content and "]" not in content


def test_proxy_streaming_realtime_rehydration(monkeypatch, httpx_mock):
    """Verifies real-time streaming rehydration of fragmented SSE chunks."""
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", False)
    test_prompt = "My name is John Doe, and my phone number is 555-0199."

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        content=(
            b'data: {"choices":[{"delta":{"content":"Welcome "}}]}\n'
            b'data: {"choices":[{"delta":{"content":"[PER"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"SON_1]! Your phone is [PHO"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"NE_1]."}}]}\n'
            b"data: [DONE]\n"
        ),
        headers={"content-type": "text/event-stream"},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": test_prompt}]},
    )

    assert response.status_code == 200
    text = response.text
    content = ""
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            data = json.loads(line[6:])
            content += data["choices"][0]["delta"]["content"]

    assert content == "Welcome John Doe! Your phone is 555-0199."
    assert "[PERSON_1]" not in content
    assert "[PHONE_1]" not in content
    assert "[PER" not in content
    assert "[PHO" not in content
