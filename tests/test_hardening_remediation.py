"""Verification suite for the TLS/SNI pinning, DPoP replay, exception-logging,
BYOK-gating, and strict-CORS remediations.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app, global_exception_handler
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.security.identity import verify_agent_identity

client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Dynamic Upstream TLS & SNI Pinning
# -----------------------------------------------------------------------------


@pytest.fixture
def upstream_key():
    """An upstream credential, because the request path returns 500 without one.

    These tests were passing only on a machine with an untracked `.env` supplying
    `UPSTREAM_API_KEY`. In CI, where no `.env` exists, the proxy answered 500
    "Upstream provider API Key is missing in proxy configuration" before reaching the
    behaviour under test, and this file has been red on main since 2026-08-30. A test
    must configure what it depends on.
    """
    original = settings.UPSTREAM_API_KEY
    settings.UPSTREAM_API_KEY = "sk-test-upstream-not-a-real-key"
    yield
    settings.UPSTREAM_API_KEY = original


@pytest.fixture
def sni_override_settings(upstream_key):
    original_override = settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE
    original_keys = settings._valid_virtual_keys_set
    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = True
    settings._valid_virtual_keys_set = frozenset(["sk-proxy-sni-test"])
    yield
    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = original_override
    settings._valid_virtual_keys_set = original_keys


def test_dynamic_upstream_override_pins_ip_but_preserves_sni(sni_override_settings, monkeypatch, httpx_mock):
    """The SSRF-validated IP is used for the socket connection, but the original FQDN
    must still be presented as the TLS SNI/certificate-verification hostname --
    otherwise any real HTTPS upstream would fail certificate validation against the
    bare IP (or force operators onto INSECURE_SKIP_VERIFY to work around it).
    """

    async def mock_resolve(hostname):
        assert hostname == "custom-upstream.example.com"
        return True, "203.0.113.10"

    monkeypatch.setattr("llm_shield_proxy.api.main._resolve_and_validate_hostname", mock_resolve)

    httpx_mock.add_response(
        method="POST",
        url="https://203.0.113.10/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-proxy-sni-test",
            "X-Upstream-Base-Url": "https://custom-upstream.example.com",
        },
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]

    # Socket connects to the pinned, SSRF-validated IP...
    assert req.url.host == "203.0.113.10"
    assert req.headers.get("host") == "custom-upstream.example.com"
    # ...but TLS SNI / certificate hostname verification is pinned to the real FQDN.
    assert req.extensions.get("sni_hostname") == "custom-upstream.example.com"


def test_default_upstream_routing_has_no_sni_override(upstream_key, httpx_mock):
    """When upstream_base isn't rewritten to an IP (the common case: no dynamic
    override, no air-gapped gateway), no sni_hostname extension should be injected --
    ordinary hostname-based httpx TLS applies unmodified.
    """
    original_keys = settings._valid_virtual_keys_set
    settings._valid_virtual_keys_set = frozenset(["sk-proxy-sni-default"])
    try:
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-proxy-sni-default"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        req = httpx_mock.get_requests()[0]
        assert not req.extensions.get("sni_hostname")
    finally:
        settings._valid_virtual_keys_set = original_keys


# -----------------------------------------------------------------------------
# 2. DPoP JTI Replay Prevention
# -----------------------------------------------------------------------------


def _mock_dpop_request() -> MagicMock:
    request = MagicMock(spec=Request)
    request.method = "POST"

    class MockURL:
        def __init__(self, s):
            self.s = s

        def replace(self, **kwargs):
            return self.s

        def __str__(self):
            return self.s

    request.url = MockURL("https://example.com/api")
    request.headers = {"Authorization": "Bearer tok", "DPoP": "dpop"}
    request.state = MagicMock()
    return request


@pytest.mark.asyncio
async def test_dpop_replay_rejected_on_reuse():
    """A second request replaying the exact same (jkt, jti) DPoP proof pair must be
    rejected with 401 'DPoP proof replayed', even though the proof is otherwise
    cryptographically valid, fresh, and correctly bound to the access token.
    """
    request = _mock_dpop_request()
    tenant_policy = {"agent_identity_enforcer": "strict", "allowed_issuers": ["https://issuer.com"]}

    with patch("llm_shield_proxy.security.identity.jwt.decode") as mock_decode, \
         patch("llm_shield_proxy.security.identity._get_signing_key", new_callable=AsyncMock), \
         patch("llm_shield_proxy.security.identity.jwt.get_unverified_header") as mock_unverified_header, \
         patch("llm_shield_proxy.security.identity.jwt.PyJWK"), \
         patch("llm_shield_proxy.security.identity.asyncio.to_thread") as mock_to_thread, \
         patch("llm_shield_proxy.security.identity._get_jwk_thumbprint") as mock_thumbprint:

        mock_decode.return_value = {"iss": "https://issuer.com"}
        mock_unverified_header.return_value = {"jwk": {"kty": "RSA"}}
        mock_thumbprint.return_value = "thumbprint-replay-test"

        def claims():
            return [
                {"cnf": {"jkt": "thumbprint-replay-test"}, "sub": "agent1"},
                {"htm": "POST", "htu": "https://example.com/api", "iat": time.time(), "jti": "replay-jti-1"},
            ]

        # First use: proof is fresh, must be accepted.
        mock_to_thread.side_effect = claims()
        await verify_agent_identity(request, tenant_policy=tenant_policy)
        assert request.state.agent_identity_claim == "agent1"

        # Second use of the identical (jkt, jti) pair must be rejected as a replay.
        mock_to_thread.side_effect = claims()
        with pytest.raises(HTTPException) as exc:
            await verify_agent_identity(request, tenant_policy=tenant_policy)
        assert exc.value.status_code == 401
        assert exc.value.detail == "DPoP proof replayed"


@pytest.mark.asyncio
async def test_dpop_distinct_jti_not_treated_as_replay():
    """Two proofs from the same key (same jkt) but distinct jti values are
    independent and must both be accepted."""
    request = _mock_dpop_request()
    tenant_policy = {"agent_identity_enforcer": "strict", "allowed_issuers": ["https://issuer.com"]}

    with patch("llm_shield_proxy.security.identity.jwt.decode") as mock_decode, \
         patch("llm_shield_proxy.security.identity._get_signing_key", new_callable=AsyncMock), \
         patch("llm_shield_proxy.security.identity.jwt.get_unverified_header") as mock_unverified_header, \
         patch("llm_shield_proxy.security.identity.jwt.PyJWK"), \
         patch("llm_shield_proxy.security.identity.asyncio.to_thread") as mock_to_thread, \
         patch("llm_shield_proxy.security.identity._get_jwk_thumbprint") as mock_thumbprint:

        mock_decode.return_value = {"iss": "https://issuer.com"}
        mock_unverified_header.return_value = {"jwk": {"kty": "RSA"}}
        mock_thumbprint.return_value = "thumbprint-distinct-test"

        for jti in ("jti-a", "jti-b"):
            mock_to_thread.side_effect = [
                {"cnf": {"jkt": "thumbprint-distinct-test"}, "sub": "agent1"},
                {"htm": "POST", "htu": "https://example.com/api", "iat": time.time(), "jti": jti},
            ]
            await verify_agent_identity(request, tenant_policy=tenant_policy)
            assert request.state.agent_identity_claim == "agent1"


@pytest.mark.asyncio
async def test_dpop_missing_jti_rejected():
    """A DPoP proof with no jti claim can never be replay-checked, so it must be
    rejected outright rather than silently accepted."""
    request = _mock_dpop_request()
    tenant_policy = {"agent_identity_enforcer": "strict", "allowed_issuers": ["https://issuer.com"]}

    with patch("llm_shield_proxy.security.identity.jwt.decode") as mock_decode, \
         patch("llm_shield_proxy.security.identity._get_signing_key", new_callable=AsyncMock), \
         patch("llm_shield_proxy.security.identity.jwt.get_unverified_header") as mock_unverified_header, \
         patch("llm_shield_proxy.security.identity.jwt.PyJWK"), \
         patch("llm_shield_proxy.security.identity.asyncio.to_thread") as mock_to_thread, \
         patch("llm_shield_proxy.security.identity._get_jwk_thumbprint") as mock_thumbprint:

        mock_decode.return_value = {"iss": "https://issuer.com"}
        mock_unverified_header.return_value = {"jwk": {"kty": "RSA"}}
        mock_thumbprint.return_value = "thumbprint-missing-jti"
        mock_to_thread.side_effect = [
            {"cnf": {"jkt": "thumbprint-missing-jti"}, "sub": "agent1"},
            {"htm": "POST", "htu": "https://example.com/api", "iat": time.time()},  # no jti
        ]

        with pytest.raises(HTTPException) as exc:
            await verify_agent_identity(request, tenant_policy=tenant_policy)
        assert exc.value.status_code == 401


# -----------------------------------------------------------------------------
# 3. Server-Side Exception Logging
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_exception_handler_logs_and_audits_without_leaking_to_client():
    """The client must only ever see the sanitized 500 body, but the real exception
    (with traceback) must reach the operational logger, and a CRITICAL entry must
    reach the signed WORM audit chain -- so an unhandled failure is never silently
    invisible server-side.
    """
    request = MagicMock(spec=Request)
    request.state.request_id = "req-exc-test"
    request.method = "POST"
    request.url.path = "/v1/chat/completions"

    exc = ValueError("boom: maybe-sensitive-detail-that-must-not-reach-the-client")

    with patch("llm_shield_proxy.api.main.logger.error") as mock_log_error, \
         patch("llm_shield_proxy.api.main.AuditLogger.log_unhandled_exception") as mock_audit:
        response = await global_exception_handler(request, exc)

    assert response.status_code == 500
    assert b"boom" not in response.body
    assert b"maybe-sensitive-detail" not in response.body

    mock_log_error.assert_called_once()
    assert mock_log_error.call_args.kwargs.get("exc_info") is exc

    mock_audit.assert_called_once_with(
        request_id="req-exc-test",
        path="/v1/chat/completions",
        method="POST",
        exc=exc,
    )


def test_audit_log_unhandled_exception_excludes_message_and_traceback():
    """AuditLogger.log_unhandled_exception must never embed str(exc) or a traceback
    in the WORM chain -- only the exception's type name -- since the audit sink
    promises zero raw PII leakage and exception messages can carry raw request
    content (e.g. a value that failed validation).
    """
    with patch.object(AuditLogger, "_enqueue_log") as mock_enqueue:
        AuditLogger.log_unhandled_exception(
            request_id="req-1",
            path="/v1/chat/completions",
            method="POST",
            exc=ValueError("contains a raw SSN 123-45-6789"),
        )

    assert mock_enqueue.call_count == 1
    severity, log_entry = mock_enqueue.call_args.args
    assert severity == "CRITICAL"
    assert log_entry["exception_type"] == "ValueError"
    serialized = str(log_entry)
    assert "123-45-6789" not in serialized
    assert "contains a raw SSN" not in serialized


# -----------------------------------------------------------------------------
# 4. BYOK Virtual Key Gating
# -----------------------------------------------------------------------------


def test_byok_prefix_alone_rejected_by_default():
    """A caller presenting a provider-shaped key (sk-proj-*) with no matching virtual
    key, and ENABLE_OPEN_BYOK_PASSTHROUGH left at its default (False), must be
    rejected with 401 rather than silently routed through the DLP pipeline and
    forwarded upstream.
    """
    original_flag = settings.ENABLE_OPEN_BYOK_PASSTHROUGH
    original_keys = settings._valid_virtual_keys_set
    settings.ENABLE_OPEN_BYOK_PASSTHROUGH = False
    settings._valid_virtual_keys_set = frozenset()
    try:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-proj-unrecognized-key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401
    finally:
        settings.ENABLE_OPEN_BYOK_PASSTHROUGH = original_flag
        settings._valid_virtual_keys_set = original_keys


def test_byok_prefix_allowed_when_explicitly_enabled(httpx_mock):
    """The same request succeeds once an operator explicitly opts into
    ENABLE_OPEN_BYOK_PASSTHROUGH."""
    original_flag = settings.ENABLE_OPEN_BYOK_PASSTHROUGH
    original_keys = settings._valid_virtual_keys_set
    settings.ENABLE_OPEN_BYOK_PASSTHROUGH = True
    settings._valid_virtual_keys_set = frozenset()
    try:
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-proj-unrecognized-key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
    finally:
        settings.ENABLE_OPEN_BYOK_PASSTHROUGH = original_flag
        settings._valid_virtual_keys_set = original_keys


# -----------------------------------------------------------------------------
# 5. Strict CORS Baseline
# -----------------------------------------------------------------------------


def test_cors_preflight_strict_default_denies_reflection():
    """With CORS_ALLOWED_ORIGINS unset, a cross-origin preflight must NOT get its
    Origin reflected back, and must NOT fall back to '*' -- either would silently
    re-open the browser-based cross-origin surface this default is meant to close.
    """
    original = settings.CORS_ALLOWED_ORIGINS
    settings.CORS_ALLOWED_ORIGINS = ""
    try:
        response = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert response.status_code == 204
        assert response.headers.get("Access-Control-Allow-Origin") == "null"
    finally:
        settings.CORS_ALLOWED_ORIGINS = original


def test_cors_preflight_explicit_allowlist_still_reflects_matching_origin():
    """An explicitly configured allowlist must still work: a listed origin gets
    reflected, and an unlisted one is still denied."""
    original = settings.CORS_ALLOWED_ORIGINS
    settings.CORS_ALLOWED_ORIGINS = "https://good.example.com"
    try:
        allowed = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://good.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert allowed.headers.get("Access-Control-Allow-Origin") == "https://good.example.com"

        denied = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://not-on-the-list.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert denied.headers.get("Access-Control-Allow-Origin") == "null"
    finally:
        settings.CORS_ALLOWED_ORIGINS = original


def test_cors_preflight_wildcard_still_supported():
    """CORS_ALLOWED_ORIGINS='*' remains an explicit, intentional opt-in to reflect
    any origin -- unlike the unset/empty case, which is strict-by-default."""
    original = settings.CORS_ALLOWED_ORIGINS
    settings.CORS_ALLOWED_ORIGINS = "*"
    try:
        response = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://anything.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert response.headers.get("Access-Control-Allow-Origin") == "*"
    finally:
        settings.CORS_ALLOWED_ORIGINS = original


# -----------------------------------------------------------------------------
# 6. Audit Drop Metrics
# -----------------------------------------------------------------------------


def test_worm_queue_drop_increments_metric_and_logs_warning():
    """When the WORM audit queue is full, the drop must be both logged (existing
    behavior) and counted via audit_events_dropped_total (new), so sustained drops
    are alertable instead of only visible by grepping logs.
    """
    from llm_shield_proxy.observability.metrics import audit_events_dropped_total

    before = audit_events_dropped_total.labels(sink="worm_chain_queue")._value.get()

    with patch.object(AuditLogger._log_queue, "put_nowait", side_effect=__import__("queue").Full):
        AuditLogger._enqueue_log("INFO", {"event": "TEST_EVENT"})

    after = audit_events_dropped_total.labels(sink="worm_chain_queue")._value.get()
    assert after == before + 1
