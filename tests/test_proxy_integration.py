import json
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_proxy_non_streaming_chat_completion(httpx_mock):
    # Mock upstream OpenAI response returning token [EMAIL_1]
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I have received your email [EMAIL_1]."
                    }
                }
            ]
        }
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "My contact is john@example.com"}
            ]
        }
    )

    assert response.status_code == 200
    res_data = response.json()
    # Upstream was sent redacted token [EMAIL_1], proxy re-hydrates back to john@example.com
    assert res_data["choices"][0]["message"]["content"] == "I have received your email john@example.com."

    # Verify upstream received redacted payload
    request = httpx_mock.get_request()
    upstream_body = json.loads(request.content.decode("utf-8"))
    assert upstream_body["messages"][0]["content"] == "My contact is [EMAIL_1]"
    assert "john@example.com" not in request.content.decode("utf-8")


def test_proxy_streaming_chat_completion(httpx_mock):
    # Mock upstream streaming SSE response returning token split across deltas
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        content=(
            b'data: {"choices":[{"delta":{"content":"Hello [EM"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"AIL_1]!"}}]}\n'
            b'data: [DONE]\n'
        ),
        headers={"content-type": "text/event-stream"}
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Email me at alice@domain.com"}
            ]
        }
    )

    assert response.status_code == 200
    content = response.text
    assert "alice@domain.com" in content
    assert "[EMAIL_1]" not in content

