"""Regression tests for the October 2026 audit: stateless crypto binding, the guard API,
masking-mode policy, vault capacity and streaming framing.

Each test names the finding it pins. All keys and PII are dummies.
"""

from __future__ import annotations

import base64
import os

import pytest
import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import request_policy_ctx, settings
from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault, zeroize_crypto_material

client = TestClient(app)

KEY_A = "vk-dummy-tenant-a-0001"
KEY_B = "vk-dummy-tenant-b-0002"
EMAIL = "carol.dummy@example.org"


@pytest.fixture
def two_keys(monkeypatch):
    keys = f"{KEY_A},{KEY_B}"
    monkeypatch.setattr(settings, "VALID_VIRTUAL_KEYS", keys)
    monkeypatch.setattr(settings, "_valid_virtual_keys_set", frozenset({KEY_A, KEY_B}))
    monkeypatch.setattr(settings, "OVERRIDE_CLIENT_AUTH", False)
    monkeypatch.setattr(settings, "ENABLE_OPEN_BYOK_PASSTHROUGH", False)
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_CLOSED")
    zeroize_crypto_material()
    monkeypatch.setattr(settings, "SHIELD_ENCRYPTION_KEY", "22" * 32)
    yield
    zeroize_crypto_material()


@pytest.fixture
def policies(tmp_path, monkeypatch):
    """Load a policies.yaml for one test and put the previous policy state back after."""
    saved = (settings._flattened_policies, getattr(settings, "_policies_loaded", False), settings._policies_mtime)
    monkeypatch.setattr(settings, "POLICIES_FILE_PATH", str(tmp_path / "policies.yaml"))

    def load(doc: dict) -> None:
        (tmp_path / "policies.yaml").write_text(yaml.safe_dump(doc))
        settings.reload_policies(force=True)

    yield load
    settings._flattened_policies, settings._policies_loaded, settings._policies_mtime = saved
    request_policy_ctx.set({})


def _h(key: str, session: str | None = None, mode: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    if session is not None:
        headers["X-Session-ID"] = session
    if mode is not None:
        headers["X-Shield-Masking-Mode"] = mode
    return headers


# crypto_vault.py:stateless-token-not-bound-to-vkid-session


def _mint_as_a() -> str:
    res = client.post(
        "/v1/guard/redact", headers=_h(KEY_A, "sess-A", "STATELESS_CRYPTO"), json={"texts": [f"contact {EMAIL}"]}
    )
    assert res.status_code == 200, res.text
    redacted = res.json()["texts"][0]
    assert EMAIL not in redacted and "[ENC_v2_" in redacted
    return redacted


def test_stateless_token_rehydrates_for_the_minting_key_and_session(two_keys):
    redacted = _mint_as_a()
    res = client.post(
        "/v1/guard/rehydrate", headers=_h(KEY_A, "sess-A", "STATELESS_CRYPTO"), json={"texts": [redacted]}
    )
    assert res.json() == {"texts": [f"contact {EMAIL}"]}


def test_other_tenant_cannot_rehydrate_a_stateless_token(two_keys):
    redacted = _mint_as_a()
    res = client.post(
        "/v1/guard/rehydrate", headers=_h(KEY_B, "sess-B", "STATELESS_CRYPTO"), json={"texts": [redacted]}
    )
    assert res.status_code == 200
    assert res.json() == {"texts": [redacted]}
    # Same session id, other key: still bound to the key.
    res = client.post(
        "/v1/guard/rehydrate", headers=_h(KEY_B, "sess-A", "STATELESS_CRYPTO"), json={"texts": [redacted]}
    )
    assert EMAIL not in res.text
    res = client.post(
        "/v1/guard/rehydrate/stream",
        headers=_h(KEY_B, mode="STATELESS_CRYPTO"),
        json={"text": redacted, "carry": "", "final": True},
    )
    assert res.status_code == 200
    assert EMAIL not in res.text


def test_same_tenant_other_session_cannot_rehydrate(two_keys):
    redacted = _mint_as_a()
    res = client.post(
        "/v1/guard/rehydrate", headers=_h(KEY_A, "sess-other", "STATELESS_CRYPTO"), json={"texts": [redacted]}
    )
    assert EMAIL not in res.text


def test_unbound_v1_tokens_are_no_longer_decrypted(two_keys):
    from llm_shield_proxy.engines.crypto_vault import get_crypto_dek

    nonce = os.urandom(12)
    ct = AESGCM(get_crypto_dek()).encrypt(nonce, EMAIL.encode(), None)
    v1 = "[ENC_v1_" + base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=") + "]"
    assert StatelessCryptoVault("vk", "s").rehydrate(f"x {v1}") == f"x {v1}"


def test_ext_proc_vaults_do_not_decrypt_each_others_tokens(two_keys):
    a, b = StatelessCryptoVault(), StatelessCryptoVault()
    token = a.get_or_create_token(EMAIL, "EMAIL")
    assert a.rehydrate(token) == EMAIL
    assert b.rehydrate(token) == token


def test_proxy_response_does_not_decrypt_another_tenants_token(two_keys, httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(settings, "UPSTREAM_API_KEY", "sk-proxy-global-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", False)
    monkeypatch.setattr(settings, "ENABLE_FINOPS_METERING", False)
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", False)
    redacted_by_a = _mint_as_a()

    def reply(content: str) -> dict:
        return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}

    httpx_mock.add_response(url="https://api.openai.com/v1/chat/completions", json=reply(redacted_by_a))
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    res = client.post("/v1/chat/completions", headers=_h(KEY_B, "sess-A", "STATELESS_CRYPTO"), json=body)
    assert res.status_code == 200, res.text
    assert EMAIL not in res.text

    httpx_mock.add_response(url="https://api.openai.com/v1/chat/completions", json=reply(redacted_by_a))
    res = client.post("/v1/chat/completions", headers=_h(KEY_A, "sess-A", "STATELESS_CRYPTO"), json=body)
    assert res.status_code == 200, res.text
    assert EMAIL in res.text


# guard_router.py:_authenticate:per-key-role-policy-and-identity-not-enforced


GUARD_ROUTES = [
    ("/v1/guard/redact", {"texts": [f"mail {EMAIL}"]}),
    ("/v1/guard/rehydrate", {"texts": ["x"]}),
    ("/v1/guard/rehydrate/stream", {"text": "x", "carry": "", "final": True}),
]


@pytest.mark.parametrize("path,body", GUARD_ROUTES)
def test_guard_refuses_a_key_the_policy_refuses(two_keys, policies, path, body):
    # Only KEY_B is mapped, there is no default_role: KEY_A is refused under FAIL_CLOSED.
    policies({"roles": {"r": {"ENABLE_FINOPS_METERING": False}}, "virtual_keys": {KEY_B: "r"}})
    res = client.post(path, headers=_h(KEY_A, "s-pol"), json=body)
    assert res.status_code == 403, res.text
    res = client.post(path, headers=_h(KEY_B, "s-pol"), json=body)
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("path,body", GUARD_ROUTES)
def test_guard_applies_the_roles_agent_identity_enforcer(two_keys, policies, path, body):
    policies({"roles": {"strict": {"agent_identity_enforcer": "strict"}}, "virtual_keys": {KEY_A: "strict"}})
    res = client.post(path, headers=_h(KEY_A, "s-id"), json=body)
    assert res.status_code == 401, res.text


def test_guard_applies_the_per_key_rate_limiter(two_keys, monkeypatch):
    from llm_shield_proxy.security.rate_limit import rate_limiter

    async def deny(_key):
        return False

    monkeypatch.setattr(rate_limiter, "acquire", deny)
    res = client.post("/v1/guard/redact", headers=_h(KEY_A, "s-rl"), json={"texts": ["x"]})
    assert res.status_code == 429


# api:uncapped-request-body:mcp_router.mcp_gateway+guard_router._read_json (guard half)


@pytest.mark.parametrize("path", [p for p, _ in GUARD_ROUTES])
def test_guard_body_over_the_cap_is_413(two_keys, monkeypatch, path):
    monkeypatch.setattr(settings, "MAX_PAYLOAD_SIZE_BYTES", 1024)
    big = {"texts": ["a" * 4096], "text": "a" * 4096, "carry": ""}
    res = client.post(path, headers=_h(KEY_A, "s-cap"), json=big)
    assert res.status_code == 413, res.text

    # No Content-Length: the streamed cumulative limit still applies.
    def chunks():
        for _ in range(8):
            yield b" " * 512
        yield b"{}"

    res = client.post(path, headers={**_h(KEY_A, "s-cap"), "Content-Type": "application/json"}, content=chunks())
    assert res.status_code == 413, res.text


# masking.py:resolve_masking_mode:client-header-overrides-role-policy


def test_resolve_masking_mode_honours_role_enforcement_and_allow_list():
    from llm_shield_proxy.engines.masking import MaskingMode, MaskingModeNotAllowed, resolve_masking_mode

    token = request_policy_ctx.set({"enforced_masking_mode": "scrub", "SHIELD_DEFAULT_MASKING_MODE": "SCRUB"})
    try:
        assert resolve_masking_mode("SYNTHETIC") is MaskingMode.SCRUB
        assert resolve_masking_mode(None) is MaskingMode.SCRUB
        request_policy_ctx.set({"enforced_masking_mode": "NOT_A_MODE"})
        assert resolve_masking_mode("SYNTHETIC") is MaskingMode.SCRUB  # fails closed
        request_policy_ctx.set({"allowed_masking_modes": ["SCRUB", "HMAC"], "SHIELD_DEFAULT_MASKING_MODE": "SCRUB"})
        assert resolve_masking_mode("hmac") is MaskingMode.HMAC
        assert resolve_masking_mode(None) is MaskingMode.SCRUB
        with pytest.raises(MaskingModeNotAllowed):
            resolve_masking_mode("STRUCTURAL_TAG")
        request_policy_ctx.set({})
        assert resolve_masking_mode("STRUCTURAL_TAG") is MaskingMode.STRUCTURAL_TAG
    finally:
        request_policy_ctx.reset(token)


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(settings, "UPSTREAM_API_KEY", "sk-proxy-global-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", False)
    monkeypatch.setattr(settings, "ENABLE_FINOPS_METERING", False)
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", False)


@pytest.mark.parametrize("header", ["SYNTHETIC", "STRUCTURAL_TAG", "HMAC"])
def test_proxy_header_cannot_override_an_enforced_scrub_role(two_keys, policies, upstream, httpx_mock, header):
    import json as _json

    from llm_shield_proxy.engines.vault import vault_store

    policies({"roles": {"pinned": {"enforced_masking_mode": "SCRUB"}}, "virtual_keys": {KEY_A: "pinned"}})
    httpx_mock.add_response(url="https://api.openai.com/v1/chat/completions", json={"choices": []})
    body = {"model": "m", "messages": [{"role": "user", "content": f"Email {EMAIL} today"}]}
    res = client.post("/v1/chat/completions", headers=_h(KEY_A, f"s-scrub-{header}", header), json=body)
    assert res.status_code == 200, res.text
    sent = _json.loads(httpx_mock.get_requests()[0].content)
    assert sent["messages"][0]["content"] == "Email [REDACTED] today"
    sessions = getattr(vault_store, "_sessions", {})
    mine = [v for k, v in sessions.items() if k.endswith(f":s-scrub-{header}")]
    assert not any(EMAIL in v.token_to_original.values() for v in mine)


def test_header_outside_allowed_masking_modes_is_403(two_keys, policies, upstream, httpx_mock):
    policies(
        {
            "roles": {"limited": {"allowed_masking_modes": ["SCRUB", "HMAC"], "SHIELD_DEFAULT_MASKING_MODE": "SCRUB"}},
            "virtual_keys": {KEY_A: "limited"},
        }
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    res = client.post("/v1/chat/completions", headers=_h(KEY_A, "s-lim", "SYNTHETIC"), json=body)
    assert res.status_code == 403
    assert httpx_mock.get_requests() == []
    res = client.post("/v1/guard/redact", headers=_h(KEY_A, "s-lim", "STRUCTURAL_TAG"), json={"texts": [EMAIL]})
    assert res.status_code == 403
    res = client.post("/v1/guard/redact", headers=_h(KEY_A, "s-lim", "SCRUB"), json={"texts": [EMAIL]})
    assert res.status_code == 200 and res.json()["texts"] == ["[REDACTED]"]


def test_guard_honours_an_enforced_mode(two_keys, policies):
    policies({"roles": {"pinned": {"enforced_masking_mode": "SCRUB"}}, "virtual_keys": {KEY_A: "pinned"}})
    res = client.post(
        "/v1/guard/redact", headers=_h(KEY_A, "s-g", "STRUCTURAL_TAG"), json={"texts": [f"mail {EMAIL}"]}
    )
    assert res.json()["texts"] == ["mail [REDACTED]"]
