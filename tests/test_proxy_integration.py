"""Integration tests for LLM-Shield-Proxy."""

import json

from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app

client = TestClient(app)


def test_proxy_non_streaming_chat_completion(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.ENABLE_SYNTHETIC_SWAPPING", False)
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.ENABLE_CANARY_TRIPWIRE", False)
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "I have received your email [EMAIL_1]."}}],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "My contact is john@example.com"}]},
    )

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["choices"][0]["message"]["content"] == "I have received your email john@example.com."

    request = httpx_mock.get_request()
    upstream_body = json.loads(request.content.decode("utf-8"))
    assert upstream_body["messages"][0]["content"] == "My contact is [EMAIL_1]"
    assert "john@example.com" not in request.content.decode("utf-8")


def test_proxy_streaming_chat_completion(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset())
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.ENABLE_SYNTHETIC_SWAPPING", False)
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        content=(
            b'data: {"choices":[{"delta":{"content":"Hello [EM"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"AIL_1]!"}}]}\n'
            b"data: [DONE]\n"
        ),
        headers={"content-type": "text/event-stream"},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock"},
        json={
            "model": "gpt-4",
            "stream": True,
            "messages": [{"role": "user", "content": "Email me at alice@domain.com"}],
        },
    )

    assert response.status_code == 200
    content = response.text
    assert "alice@domain.com" in content
    assert "[EMAIL_1]" not in content


def test_health_and_livez_check_endpoints():
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    res_livez = client.get("/livez")
    assert res_livez.status_code == 200
    assert res_livez.json()["status"] == "ok"

    res_healthz = client.get("/healthz")
    assert res_healthz.status_code == 200

    res_readyz = client.get("/readyz")
    assert res_readyz.status_code == 200
    assert res_readyz.json()["status"] == "ready"

    res_metrics = client.get("/metrics")
    assert res_metrics.status_code == 200


def test_cors_preflight_options():
    # CORS_ALLOWED_ORIGINS is unset by default: preflight now denies cross-origin
    # access ("null") rather than reflecting "*" -- see test_hardening_remediation.py
    # for the explicit strict-default/allowlist/wildcard-opt-in coverage.
    res = client.options("/v1/chat/completions")
    assert res.status_code == 204
    assert res.headers["access-control-allow-origin"] == "null"
    assert "OPTIONS" in res.headers["access-control-allow-methods"]
    assert "Authorization" in res.headers["access-control-allow-headers"]


def test_inbound_auth_validation(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-finance")
    monkeypatch.setattr(
        "llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset({"sk-proxy-finance"})
    )

    # Missing header
    res_missing = client.post("/v1/chat/completions", json={"model": "gpt-4", "messages": []})
    assert res_missing.status_code == 401

    # Invalid key
    res_invalid = client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer sk-proxy-hr"}, json={"model": "gpt-4", "messages": []}
    )
    assert res_invalid.status_code == 401


def test_header_swapping_and_byok(monkeypatch, httpx_mock):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.VALID_VIRTUAL_KEYS", "sk-proxy-dev")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset({"sk-proxy-dev"}))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_API_KEY", "central-gemini-key")

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={"id": "123", "choices": [{"message": {"content": "ok"}}]},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={"id": "124", "choices": [{"message": {"content": "ok"}}]},
    )

    # Virtual Key Swapping
    res1 = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-dev"},
        json={"model": "gpt-4", "messages": []},
    )
    assert res1.status_code == 200
    req1 = httpx_mock.get_requests()[0]
    assert req1.headers["authorization"] == "Bearer central-gemini-key"

    # BYOK Passthrough
    res2 = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-user"},
        json={"model": "gpt-4", "messages": []},
    )
    assert res2.status_code == 200
    req2 = httpx_mock.get_requests()[1]
    assert req2.headers["authorization"] == "Bearer sk-proj-user"


def test_missing_upstream_key_returns_clean_error(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["sk-proxy-test"]))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.OPENAI_API_KEY", None)
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_API_KEY", None)

    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proxy-test"},
        json={"model": "gpt-4", "messages": []},
    )
    assert res.status_code == 500
    assert "error" in res.json()
    assert res.json()["error"]["type"] == "proxy_misconfiguration"
    assert "Upstream provider API Key is missing in proxy configuration" in res.json()["error"]["message"]


def test_missing_upstream_key_error_names_the_setting(monkeypatch):
    """A 500 that does not say which variable to set sent people to the wrong one."""
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["sk-proxy-test"]))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.OPENAI_API_KEY", None)
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_API_KEY", None)
    body = {"model": "gpt-4", "messages": []}
    headers = {"Authorization": "Bearer sk-proxy-test"}

    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "https://api.openai.com")
    message = client.post("/v1/chat/completions", headers=headers, json=body).json()["error"]["message"]
    assert message.endswith("Set OPENAI_API_KEY or UPSTREAM_API_KEY.")

    monkeypatch.setattr("llm_shield_proxy.core.config.settings.UPSTREAM_BASE_URL", "http://127.0.0.1:8765/v1")
    message = client.post("/v1/chat/completions", headers=headers, json=body).json()["error"]["message"]
    assert message.endswith("Set UPSTREAM_API_KEY.")


def test_rejected_client_key_error_names_the_setting(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["sk-proxy-test"]))
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.ENABLE_OPEN_BYOK_PASSTHROUGH", False)
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.OVERRIDE_CLIENT_AUTH", False)

    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer not-a-listed-key"},
        json={"model": "gpt-4", "messages": []},
    )
    assert res.status_code == 401
    assert "VALID_VIRTUAL_KEYS" in res.json()["error"]["message"]


def test_startup_warns_when_no_client_key_can_authenticate(monkeypatch, caplog):
    from llm_shield_proxy.api.main import warn_if_no_client_can_authenticate

    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset())
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.ENABLE_OPEN_BYOK_PASSTHROUGH", False)
    monkeypatch.setattr("llm_shield_proxy.core.config.settings.OVERRIDE_CLIENT_AUTH", False)
    with caplog.at_level("WARNING", logger="llm_shield_proxy.api.main"):
        warn_if_no_client_can_authenticate()
    assert "every proxied request will be rejected with 401" in caplog.text

    caplog.clear()
    monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set", frozenset(["k"]))
    with caplog.at_level("WARNING", logger="llm_shield_proxy.api.main"):
        warn_if_no_client_can_authenticate()
    assert "rejected with 401" not in caplog.text


def _ext_proc_refused(monkeypatch, *, explicit: bool):
    """ENABLE_EXT_PROC on, as by default, with the socket path refused like /var/run for a user."""
    import llm_shield_proxy.api.main as main_module
    from llm_shield_proxy.core.config import settings

    async def refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(main_module, "_start_ext_proc", refuse)
    base = settings._base  # the proxy forwards attribute writes; fields-set lives on the model
    fields_set = set(base.model_fields_set)
    settings.ENABLE_EXT_PROC = True
    marked = fields_set | {"ENABLE_EXT_PROC"} if explicit else fields_set - {"ENABLE_EXT_PROC"}
    object.__setattr__(base, "__pydantic_fields_set__", marked)
    return fields_set


def test_default_ext_proc_that_cannot_bind_does_not_stop_the_proxy(monkeypatch, caplog):
    """`pip install llm-shield-proxy && llm-shield-proxy` as a normal user on Linux died at
    startup: the default-on ext_proc listener wanted /var/run/llm-shield."""
    from llm_shield_proxy.core.config import settings

    original = _ext_proc_refused(monkeypatch, explicit=False)
    try:
        with caplog.at_level("WARNING", logger="llm_shield_proxy.api.main"):
            with TestClient(app) as started:
                assert started.get("/healthz").status_code == 200
        assert "ext_proc listener not started (PermissionError" in caplog.text
    finally:
        settings.ENABLE_EXT_PROC = False
        object.__setattr__(settings._base, "__pydantic_fields_set__", original)


def test_explicit_ext_proc_that_cannot_bind_still_fails_startup(monkeypatch):
    import pytest

    from llm_shield_proxy.core.config import settings

    original = _ext_proc_refused(monkeypatch, explicit=True)
    try:
        with pytest.raises(PermissionError):
            with TestClient(app):
                pass
    finally:
        settings.ENABLE_EXT_PROC = False
        object.__setattr__(settings._base, "__pydantic_fields_set__", original)
