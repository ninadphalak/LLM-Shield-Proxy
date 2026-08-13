import json
import pytest
from fastapi.testclient import TestClient
from llm_shield_proxy.main import app

client = TestClient(app)


def test_proxy_non_streaming_chat_completion(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
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
        headers={"Authorization": "Bearer sk-proj-mock"},
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


def test_proxy_streaming_chat_completion(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.config.settings.valid_virtual_keys_set", frozenset())
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
        headers={"Authorization": "Bearer sk-proj-mock"},
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


def test_health_and_livez_check_endpoints():
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"
    assert res_health.json()["service"] == "llm-shield-proxy"

    res_livez = client.get("/livez")
    assert res_livez.status_code == 200
    assert res_livez.json()["status"] == "ok"

    res_healthz = client.get("/healthz")
    assert res_healthz.status_code == 200

    res_metrics = client.get("/metrics")
    assert res_metrics.status_code == 200


def test_cors_preflight_options():
    res = client.options("/v1/chat/completions")
    assert res.status_code == 204
    assert res.headers["access-control-allow-origin"] == "*"
    assert "OPTIONS" in res.headers["access-control-allow-methods"]
    assert "Authorization" in res.headers["access-control-allow-headers"]


def test_inbound_auth_validation(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-finance")
    monkeypatch.setattr("llm_shield_proxy.config.settings.valid_virtual_keys_set", frozenset({"sk-proxy-finance"}))
    
    # Missing header
    res_missing = client.post("/v1/chat/completions", json={"model": "gpt-4", "messages": []})
    assert res_missing.status_code == 401
    
    # Invalid key
    res_invalid = client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-proxy-hr"}, json={"model": "gpt-4", "messages": []})
    assert res_invalid.status_code == 401


def test_header_swapping_and_byok(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-dev")
    monkeypatch.setattr("llm_shield_proxy.config.settings.valid_virtual_keys_set", frozenset({"sk-proxy-dev"}))
    monkeypatch.setattr("llm_shield_proxy.config.settings.UPSTREAM_API_KEY", "central-gemini-key")
    
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={"id": "123", "choices": [{"message": {"content": "ok"}}]}
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={"id": "124", "choices": [{"message": {"content": "ok"}}]}
    )
    
    # Virtual Key Swapping
    res1 = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-dev"},
        json={"model": "gpt-4", "messages": []}
    )
    assert res1.status_code == 200
    req1 = httpx_mock.get_requests()[0]
    assert req1.headers["authorization"] == "Bearer central-gemini-key"
    
    # BYOK Passthrough
    res2 = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-user"},
        json={"model": "gpt-4", "messages": []}
    )
    assert res2.status_code == 200
    req2 = httpx_mock.get_requests()[1]
    assert req2.headers["authorization"] == "Bearer sk-proj-user"


def test_missing_upstream_key_returns_clean_error(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.config.settings.valid_virtual_keys_set", frozenset(["sk-proxy-test"]))
    import httpx
    
    def raise_500(*args, **kwargs):
        resp = httpx.Response(500, request=httpx.Request("POST", "https://api.openai.com"))
        raise httpx.HTTPStatusError("500 Server Error", request=resp.request, response=resp)

    httpx_mock.add_callback(raise_500, url="https://api.openai.com/v1/chat/completions")

    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-test"},
        json={"model": "gpt-4", "messages": []}
    )
    # The proxy translates HTTPStatusError to its status code (500)
    assert res.status_code == 500
    assert "error" in res.json()
    assert res.json()["error"]["type"] == "upstream_error"


