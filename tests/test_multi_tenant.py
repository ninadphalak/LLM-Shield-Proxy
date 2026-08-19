"""Multi-tenant provider routing and dynamic upstream endpoint tests."""

from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app

client = TestClient(app)


def test_multi_provider_routing_openai(monkeypatch, httpx_mock):
    """Tests routing to OpenAI upstream with virtual key resolution."""
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-tenant-a")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["sk-proxy-tenant-a"]))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.OPENAI_API_KEY", "sk-central-openai-key")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={"id": "openai-1", "choices": [{"message": {"content": "Hello from OpenAI"}}]},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-tenant-a"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hello from OpenAI"
    request = httpx_mock.get_request()
    assert request.headers["authorization"] == "Bearer sk-central-openai-key"


def test_multi_provider_routing_gemini(monkeypatch, httpx_mock):
    """Tests routing to Gemini OpenAI-compatible upstream endpoint."""
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-tenant-b")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["sk-proxy-tenant-b"]))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.GEMINI_API_KEY", "AIza-central-gemini-key")
    monkeypatch.setattr(
        "llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
    )

    httpx_mock.add_response(
        method="POST",
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        json={"id": "gemini-1", "choices": [{"message": {"content": "Hello from Gemini"}}]},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-tenant-b"},
        json={"model": "gemini-1.5-flash", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hello from Gemini"
    request = httpx_mock.get_request()
    assert request.headers["authorization"] == "Bearer AIza-central-gemini-key"
