"""Audit 2026-10: the shim's vault session is bound to the LiteLLM caller, not only the call id.

Finding examples/integrations/litellm/litellm_guardrail_shim.py:_session_id:litellm_call_id-vault-scope.
Every LiteLLM user shares the shim's one Shield virtual key, so a session id built only from
``litellm_call_id`` let a second user who reused the call id restore the first user's values.
Dummy keys and a dummy email only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIM_PATH = REPO_ROOT / "examples" / "integrations" / "litellm" / "litellm_guardrail_shim.py"
SHIM_KEY = "dummy-shim-key"
ENDPOINT = "/beta/litellm_basic_guardrail_api"


def _load(monkeypatch):
    monkeypatch.setenv("LITELLM_GUARDRAIL_SHIM_KEY", SHIM_KEY)
    monkeypatch.setenv("SHIELD_API_KEY", "dummy-shield-vk")
    monkeypatch.setenv("SHIELD_BASE_URL", "http://shield.local")
    monkeypatch.setenv("SHIM_STREAMING_REHYDRATION", "false")
    name = "litellm_guardrail_shim_audit"
    spec = importlib.util.spec_from_file_location(name, SHIM_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _body(input_type, texts, call_id, key_hash=None):
    body = {"texts": texts, "input_type": input_type, "litellm_call_id": call_id}
    if key_hash is not None:
        body["request_data"] = {"user_api_key_hash": key_hash}
    return body


def test_the_session_depends_on_the_caller_identity(monkeypatch):
    module = _load(monkeypatch)
    seen = []

    async def fake(path, session_id, payload):
        seen.append(session_id)
        return {"texts": payload["texts"]}

    monkeypatch.setattr(module, "_call_shield", fake)
    client = TestClient(module.app)
    for body in (
        _body("request", ["x"], "call-1", "hash-A"),
        _body("response", ["x"], "call-1", "hash-A"),
        _body("response", ["x"], "call-1", "hash-B"),
        _body("response", ["x"], "call-1"),
    ):
        assert client.post(ENDPOINT, headers={"x-api-key": SHIM_KEY}, json=body).status_code == 200
    a_request, a_response, b_response, anonymous = seen
    assert a_request == a_response, "both hooks of one call share the vault"
    assert a_request != b_response, "another LiteLLM key never gets that vault"
    assert "call-1" not in a_request and "hash-A" not in a_request
    assert a_request.startswith("litellm_u_")
    assert anonymous == "litellm-call-1", "no identity: the documented call-id fallback"


def test_another_litellm_user_reusing_the_call_id_does_not_get_the_plaintext(monkeypatch):
    """End to end through the real in-process Shield, as in the audit's evidence."""
    pytest.importorskip("llm_shield_proxy.api.main")
    module = _load(monkeypatch)
    from llm_shield_proxy.api.main import app as shield_app
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "valid_virtual_keys_set", {"dummy-shield-vk"})
    real_client = httpx.AsyncClient

    class ASGIClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.ASGITransport(app=shield_app)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", ASGIClient)
    client = TestClient(module.app)
    headers = {"x-api-key": SHIM_KEY}

    redacted = client.post(
        ENDPOINT, headers=headers,
        json=_body("request", ["Please email alice.dummy@example.com today"], "shared-call-id-1", "hash-user-A"),
    ).json()
    assert redacted["action"] == "GUARDRAIL_INTERVENED"
    stand_in = redacted["texts"][0]
    assert "alice.dummy@example.com" not in stand_in

    other = client.post(
        ENDPOINT, headers=headers,
        json=_body("response", ["Echo: " + stand_in], "shared-call-id-1", "hash-user-B"),
    ).json()
    assert not any("alice.dummy@example.com" in t for t in (other.get("texts") or []))

    own = client.post(
        ENDPOINT, headers=headers,
        json=_body("response", ["Echo: " + stand_in], "shared-call-id-1", "hash-user-A"),
    ).json()
    assert any("alice.dummy@example.com" in t for t in (own.get("texts") or [])), "the caller's own reply still restores"
