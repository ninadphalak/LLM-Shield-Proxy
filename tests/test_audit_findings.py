"""Regression tests for the issues raised in AUDIT_FINDINGS_deepseek.md.

Covers the fixes that are easiest to assert directly:

- C1: the client-supplied ``X-Shield-Fallback-Url`` must not enable SSRF.
- C2: non-streaming responses must redact model-originated PII when enabled.
- C3: the global exception handler must not log raw exception text.
- M1: malformed ``Content-Length`` must be a clean 400, not a crash/silent drop.
- M4: short, padded base64-encoded PII must be detected.

C3's MCP portion lives in test_mcp_routing.py; C4 lives in test_vault.py.
"""

from __future__ import annotations

import base64
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)


# ---------------------------------------------------------------------------
# C1: X-Shield-Fallback-Url SSRF
# ---------------------------------------------------------------------------


def test_client_fallback_url_is_ignored_when_override_disabled(monkeypatch, httpx_mock):
    """A client fallback header must not route anywhere unless the override gate is open."""
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "ALLOW_CLIENT_UPSTREAM_OVERRIDE", False)
    monkeypatch.setattr(settings, "FALLBACK_BASE_URL", None)

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        status_code=503,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-proj-mock-key",
            "X-Shield-Fallback-Url": "http://169.254.169.254/latest/meta-data",
        },
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    # The client-supplied metadata fallback must never have been dialed.
    for req in httpx_mock.get_requests():
        assert "169.254.169.254" not in str(req.url)


def test_client_fallback_url_resolving_to_metadata_is_rejected(monkeypatch):
    """When the override gate IS open, the fallback must still clear SSRF pinning."""
    monkeypatch.setattr(settings, "ALLOW_CLIENT_UPSTREAM_OVERRIDE", True)

    async def _reject(hostname):
        return False, None

    monkeypatch.setattr("llm_shield_proxy.api.main._resolve_and_validate_hostname", _reject)

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-proj-mock-key",
            "X-Shield-Fallback-Url": "http://169.254.169.254/latest/meta-data",
        },
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# C2: non-streaming model-originated PII
# ---------------------------------------------------------------------------


def test_non_streaming_response_redacts_model_originated_pii():
    from llm_shield_proxy.api.main import _redact_model_originated_json_response
    from llm_shield_proxy.engines.vault import Vault

    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("user@example.com", "EMAIL")

    res = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"Yours: {token}; model-invented: model@example.com",
                }
            }
        ]
    }
    out = _redact_model_originated_json_response(res, vault)
    content = out["choices"][0]["message"]["content"]

    assert token in content, "the caller's own token must survive for later rehydration"
    assert "model@example.com" not in content
    assert "[EMAIL_REDACTED]" in content


def test_non_streaming_response_redacts_model_pii_end_to_end(monkeypatch, httpx_mock):
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", False)
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", False)

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        json={
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant", "content": "Ref model@example.com and yours [EMAIL_1]"}}],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "My email is john.doe@example.com"}]},
    )

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert "model@example.com" not in content, "model-originated PII reached the client"
    assert "john.doe@example.com" in content, "the caller's own value was not restored"
    assert "[EMAIL_1]" not in content


# ---------------------------------------------------------------------------
# C3: no raw exception text in operational logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_exception_handler_does_not_log_raw_exception(caplog):
    from types import SimpleNamespace

    from llm_shield_proxy.api.main import global_exception_handler

    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/v1/chat/completions"),
        state=SimpleNamespace(request_id="req-123"),
    )
    secret = "secret-ssn-123-45-6789"
    with caplog.at_level(logging.ERROR, logger="llm_shield_proxy.api.main"):
        await global_exception_handler(request, ValueError(secret))

    assert secret not in caplog.text
    assert "ValueError" in caplog.text


# ---------------------------------------------------------------------------
# M1: malformed Content-Length
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["not-a-number", "-5"])
async def test_malformed_content_length_is_a_clean_400(value):
    from llm_shield_proxy.api.main import read_body_with_limit

    class FakeRequest:
        headers = {"content-length": value}

    with pytest.raises(HTTPException) as exc_info:
        await read_body_with_limit(FakeRequest())
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# M4: short, padded base64
# ---------------------------------------------------------------------------


def test_short_padded_base64_pii_is_detected():
    from llm_shield_proxy.engines.pii_engine import PIIEngine
    from llm_shield_proxy.engines.vault import Vault

    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)

    # 8 bytes -> 12 base64 chars with '=' padding (previously escaped the scan).
    encoded_phone = base64.b64encode(b"555-0199").decode()
    redacted = engine.redact_text(f"call {encoded_phone} now", vault)
    assert encoded_phone not in redacted

    # 11 bytes -> 16 base64 chars with '=' padding (an SSN).
    encoded_ssn = base64.b64encode(b"123-45-6789").decode()
    redacted = engine.redact_text(f"ssn {encoded_ssn} here", vault)
    assert encoded_ssn not in redacted

