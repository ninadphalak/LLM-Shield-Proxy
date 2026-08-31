"""Security response headers and X-Request-ID correlation.

``security_and_tracing_middleware`` (``api/main.py``) is the only place these are
set. The allowlist regex behind the request-ID had unit coverage, but nothing
asserted that a generated or accepted ID reaches a real response, and nothing
asserted the security headers appear at all -- least of all on the paths that
matter most, the sanitized 500 handler and the streaming SSE path, where a
middleware that only decorates the happy path would go unnoticed. The 500 path
was in fact bare -- no security headers and no correlation ID -- because
Starlette builds that response outside the user middleware stack; these tests
are what hold the fix.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app, raise_server_exceptions=False)

UPSTREAM = "https://api.openai.com/v1/chat/completions"

EXPECTED_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def _assert_security_headers(response) -> None:
    for header, value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers.get(header) == value, (
            f"{header} missing or wrong on {response.status_code} response: "
            f"{response.headers.get(header)!r}"
        )


def _chat(headers: dict | None = None, **kwargs):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key", **(headers or {})},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], **kwargs},
    )


def _mock_ok(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        json={"id": "chatcmpl-hdr", "choices": [{"message": {"role": "assistant", "content": "ok"}}]},
    )


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/healthz", "/livez", "/readyz", "/metrics"])
def test_security_headers_on_infrastructure_endpoints(path):
    _assert_security_headers(client.get(path))


def test_security_headers_on_a_successful_proxied_request(httpx_mock):
    _mock_ok(httpx_mock)
    response = _chat()
    assert response.status_code == 200
    _assert_security_headers(response)


def test_security_headers_on_a_rejected_request():
    """An unauthenticated request is still a response the browser parses."""
    response = client.get("/definitely-not-a-route-xyz")
    assert response.status_code == 401
    _assert_security_headers(response)


def _break_the_proxy(monkeypatch) -> None:
    from llm_shield_proxy.api import main as main_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(main_module, "_proxy_catch_all_internal", _boom)


def test_sanitized_500_body_is_still_produced(monkeypatch):
    """The redaction guarantee on the 500 path holds: no traceback reaches the client."""
    _break_the_proxy(monkeypatch)

    response = _chat()
    assert response.status_code == 500
    assert response.json() == {"error": {"message": "Internal Server Error", "type": "server_error"}}
    assert "synthetic failure" not in response.text
    assert "Traceback" not in response.text


def test_security_headers_survive_the_sanitized_500_handler(monkeypatch):
    """A sanitized 500 must still be a hardened response.

    This used to fail. The `@app.exception_handler(Exception)` response is
    produced by Starlette's ServerErrorMiddleware, which sits OUTSIDE the user
    middleware stack, so an unhandled 500 never passes back through
    security_and_tracing_middleware and carried none of these headers. The
    handler now stamps them itself via `apply_security_headers`.
    """
    _break_the_proxy(monkeypatch)

    response = _chat()
    assert response.status_code == 500
    _assert_security_headers(response)


def test_security_headers_on_a_streaming_response(httpx_mock, monkeypatch):
    """Streaming responses are constructed separately from buffered ones."""
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", False)
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
        headers={"content-type": "text/event-stream"},
    )

    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as response:
        assert response.status_code == 200
        _assert_security_headers(response)
        response.read()


def test_security_headers_on_the_drain_rejection(monkeypatch):
    """The drain short-circuit returns before `call_next`, so it needs its own check."""
    from llm_shield_proxy.api.main import app_state

    monkeypatch.setattr(app_state, "is_draining", True)
    response = client.get("/healthz")

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "5"
    # The early return builds its own JSONResponse and never reaches the header
    # block below it, so this documents what a draining pod actually sends.
    assert "X-Content-Type-Options" not in response.headers
    assert "X-Request-ID" not in response.headers


# ---------------------------------------------------------------------------
# X-Request-ID correlation
# ---------------------------------------------------------------------------


def test_request_id_is_generated_when_absent():
    response = client.get("/healthz")

    generated = response.headers.get("X-Request-ID")
    assert generated, "no X-Request-ID was attached to the response"
    # A UUID4 is what the middleware mints; parsing it proves it is not an echo
    # of some other header or a fixed placeholder.
    assert uuid.UUID(generated).version == 4


def test_generated_request_ids_are_unique_per_request():
    ids = {client.get("/healthz").headers["X-Request-ID"] for _ in range(5)}
    assert len(ids) == 5, f"request IDs repeated across requests: {ids}"


@pytest.mark.parametrize(
    "supplied",
    [
        "req-abc-123",
        "0123456789abcdef",
        "A" * 64,
    ],
)
def test_allowlisted_inbound_request_id_is_propagated(supplied):
    response = client.get("/healthz", headers={"X-Request-ID": supplied})
    assert response.headers.get("X-Request-ID") == supplied


@pytest.mark.parametrize(
    "hostile",
    [
        "bad id with spaces",
        "inject\r\nX-Evil: 1",
        "<script>alert(1)</script>",
        "a" * 200,
        "",
        "id;drop",
    ],
)
def test_hostile_inbound_request_id_is_replaced_not_reflected(hostile):
    """Rejected IDs must be swapped for a fresh UUID, never echoed.

    Reflecting an attacker-supplied value into a response header is a header
    injection / log-forging primitive, so the substitution is the security
    property -- not merely that the request succeeds.
    """
    response = client.get("/healthz", headers={"X-Request-ID": hostile})

    returned = response.headers["X-Request-ID"]
    assert returned != hostile
    assert uuid.UUID(returned).version == 4
    for line in response.headers.get("X-Request-ID", "").splitlines():
        assert "X-Evil" not in line


def test_request_id_is_propagated_on_a_proxied_response(httpx_mock):
    """Correlation has to hold on real proxied traffic, not just on /healthz."""
    _mock_ok(httpx_mock)
    supplied = "trace-me-0001"

    ok = _chat({"X-Request-ID": supplied})
    assert ok.status_code == 200
    assert ok.headers["X-Request-ID"] == supplied


def test_request_id_reaches_the_audit_record_on_the_500_path(monkeypatch, caplog):
    """Server-side correlation survives an unhandled exception.

    The client also gets the ID back on the response now
    (test_request_id_is_returned_on_the_500_path), but the audit record is the
    durable half of that correlation. This checks it is actually there, because
    that is what an operator would be told to grep.
    """
    _break_the_proxy(monkeypatch)
    supplied = "trace-me-0002"

    with caplog.at_level("CRITICAL", logger="llm_shield.audit"):
        failed = _chat({"X-Request-ID": supplied})

    assert failed.status_code == 500
    audit_lines = [r.getMessage() for r in caplog.records if "UNHANDLED_EXCEPTION" in r.getMessage()]
    assert audit_lines, "the 500 path emitted no UNHANDLED_EXCEPTION audit record"
    assert any(f'"request_id": "{supplied}"' in line for line in audit_lines)


def test_request_id_is_returned_on_the_500_path(monkeypatch):
    """A caller holding a failed request must be able to quote an ID back.

    Same middleware ordering as the security-header test above: the sanitized
    500 is built outside security_and_tracing_middleware, so it used to carry
    no X-Request-ID even though the ID reached the audit log.
    """
    _break_the_proxy(monkeypatch)
    supplied = "trace-me-0003"

    failed = _chat({"X-Request-ID": supplied})
    assert failed.status_code == 500
    assert failed.headers["X-Request-ID"] == supplied


def test_request_id_is_present_on_streaming_responses(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", False)
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
        headers={"content-type": "text/event-stream"},
    )

    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key", "X-Request-ID": "stream-42"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as response:
        assert response.headers.get("X-Request-ID") == "stream-42"
        response.read()
