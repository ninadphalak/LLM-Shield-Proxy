"""The capture server must be verifiable and deployable by the tester.

Two changes are pinned here.

**The post-bind self-probe.** Round 6 tried to stop a stolen capture port by clearing
``allow_reuse_address`` on Windows. Measured on Windows 11, that does not prevent the
hijack: the steal happens when the two sockets bind DIFFERENT addresses, where the
flag is irrelevant, and the matching-address case was already refused before the flag
existed. With the flag applied, a pre-existing loopback listener still took every
request while the capture recorded zero. The replacement probes the channel instead of
tuning the flag: the harness sends one request to its own capture URL and aborts unless
the capture recorded it.

**The public capture mode.** A loopback-only capture cannot be reached by a hosted
gateway, which made every hosted target unmeasurable. The capture can now be bound to a
reachable interface and fronted by the tester's own tunnel, with a bearer token so
arbitrary internet traffic cannot enter the capture record and unattributed hits
recorded and reported rather than dropped. The project does not host a capture service.
"""

import errno
import io
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pii_leak_benchmark.http_profile import (
    CaptureUnreachableError,
    extract_fixture,
    run_http_conformance,
)


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


# No module-level mask any more: the fixture varies per run, so each mock
# recovers the values from the prompt it received. See extract_fixture.


class _EchoGateway(BaseHTTPRequestHandler):
    """A conforming gateway: redacts before forwarding, rehydrates on the way back.

    It must genuinely redact. A helper that forwarded the fixture raw would leak, and
    every assertion about capture MODE below would be testing a leaking gateway.
    """

    protocol_version = "HTTP/1.1"
    capture_port = 0
    capture_headers: dict = {}

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802
        import urllib.request

        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        prompt = payload["messages"][-1]["content"]
        # Values vary per run: recover them from the prompt by format.
        RAW = list(extract_fixture(prompt).values())
        MASK = {value: f"[TOK_{index}]" for index, value in enumerate(RAW)}
        masked = prompt
        for raw, token in MASK.items():
            masked = masked.replace(raw, token)
        payload["messages"][-1]["content"] = masked
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.capture_port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **self.capture_headers},
        )
        try:
            urllib.request.urlopen(request, timeout=30).read()
        except Exception:  # noqa: BLE001 - the capture's answer is irrelevant here
            pass
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        for character in prompt:
            event = {"choices": [{"delta": {"content": character}}]}
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _gateway(port, headers=None):
    return type(
        "Gateway",
        (_EchoGateway,),
        {"capture_port": port, "capture_headers": headers or {}},
    )


def _run(handler_class, port, **kwargs):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=port,
            **kwargs,
        )
    finally:
        server.shutdown()
        server.server_close()


class _Squatter(BaseHTTPRequestHandler):
    """Another process already listening on the capture port."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        body = b'{"squatter": true}'
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


# --------------------------------------------------------------------------
# The self-probe
# --------------------------------------------------------------------------


def test_a_held_capture_port_fails_the_run_closed(capture_port):
    """A pre-existing listener on the capture port must never yield a report.

    The two platforms fail closed at DIFFERENT points, and both are correct:

    - POSIX refuses the second bind outright with ``EADDRINUSE``, so the run ends
      before the probe is reached. The CLI turns that into "Benchmark failed", exit 2.
    - Windows lets the bind succeed (``allow_reuse_address`` is left at HTTPServer's
      default, which is exactly why clearing that flag was not a fix) and the squatter
      takes every request. The post-bind self-probe is what stops it there.

    What must hold on both is the outcome: an OSError and no report.
    ``CaptureUnreachableError`` subclasses OSError deliberately so one except clause
    covers both. Asserting the Windows path specifically is what made this test fail
    on Linux the first time it ever ran there.
    """
    squatter = ThreadingHTTPServer(("127.0.0.1", capture_port), _Squatter)
    threading.Thread(target=squatter.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OSError) as excinfo:
            _run(_gateway(capture_port), capture_port)
    finally:
        squatter.shutdown()
        squatter.server_close()

    error = excinfo.value
    if isinstance(error, CaptureUnreachableError):
        assert "self-probe" in str(error)
    else:
        assert error.errno == errno.EADDRINUSE, error


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "POSIX refuses the duplicate bind with EADDRINUSE, so the hijack this mutation "
        "demonstrates cannot occur there -- which is why the probe exists for Windows, "
        "where the bind succeeds and the squatter silently takes the traffic."
    ),
)
def test_reverting_the_self_probe_lets_the_hijack_report_a_gateway_failure(
    capture_port, monkeypatch
):
    """Controlled mutation: without the probe, the hijack is invisible again.

    This is the defect being fixed. A squatter takes the traffic, the capture records
    nothing, and the harness emits a schema-valid report failing the boundary check
    against a gateway that did nothing wrong. If this test ever stops reproducing that,
    the probe is no longer the thing standing between a hijacked port and a false
    leak result.
    """
    monkeypatch.setattr(
        "pii_leak_benchmark.http_profile._self_probe",
        lambda *_args, **_kwargs: {
            "performed": True,
            "url": "disabled",
            "recorded": True,
            "round_trip_ms": 0.0,
        },
    )
    squatter = ThreadingHTTPServer(("127.0.0.1", capture_port), _Squatter)
    threading.Thread(target=squatter.serve_forever, daemon=True).start()
    try:
        report = _run(_gateway(capture_port), capture_port)
    finally:
        squatter.shutdown()
        squatter.server_close()
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["captured_requests"] == 0
    assert boundary["passed"] is False
    assert report["passed"] is False


def test_self_probe_is_recorded_in_the_report(capture_port):
    report = _run(_gateway(capture_port), capture_port)
    probe = report["capture"]["self_probe"]
    assert probe["performed"] is True
    assert probe["recorded"] is True
    assert probe["round_trip_ms"] >= 0


def test_self_probe_does_not_pollute_the_capture_record(capture_port):
    """The probe must not move a single number a verdict is computed from."""
    report = _run(_gateway(capture_port), capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["captured_requests"] == 1
    assert boundary["correlated_requests"] == 1
    assert boundary["uninspectable_requests"] == 0
    assert boundary["leaked_entity_types"] == []
    # The probe path must appear nowhere in the observed surface.
    assert all(
        "conformance_capture_probe" not in path
        for path in boundary["upstream_paths_observed"]
    )
    assert report["passed"] is True, report["checks"]


def test_self_probe_path_carries_no_digits():
    """A digit in the probe path would join the cross-request digit haystacks.

    The SSN and card needles are matched against ordered per-channel joins with
    separators stripped. Harness-generated traffic must be incapable of contributing
    to that reassembly, not merely unlikely to.
    """
    from pii_leak_benchmark.http_profile import (
        _PROBE_PATH_TEMPLATE,
        _make_probe_token,
    )

    for _ in range(200):
        token = _make_probe_token()
        assert token.isalpha() and token.islower()
    assert not any(character.isdigit() for character in _PROBE_PATH_TEMPLATE)


# --------------------------------------------------------------------------
# Public capture mode
# --------------------------------------------------------------------------


def test_public_mode_requires_a_capture_token(capture_port):
    """An unauthenticated capture on a reachable interface is not offered."""
    with pytest.raises(ValueError, match="capture_token is required"):
        run_http_conformance(
            "http://127.0.0.1:1/v1",
            iterations=1,
            capture_port=capture_port,
            capture_host="0.0.0.0",
            capture_public_url=f"http://example.invalid:{capture_port}/v1",
        )


def test_non_loopback_bind_requires_an_advertised_url(capture_port):
    """A wildcard bind has no address a target can be configured with."""
    with pytest.raises(ValueError, match="capture_public_url is required"):
        run_http_conformance(
            "http://127.0.0.1:1/v1",
            iterations=1,
            capture_port=capture_port,
            capture_host="0.0.0.0",
            capture_token="token",
        )


def test_loopback_mode_is_reported_and_needs_no_token(capture_port):
    report = _run(_gateway(capture_port), capture_port)
    assert report["capture"]["mode"] == "loopback"
    assert report["capture"]["authentication_required"] is False
    assert report["checks"]["configured_upstream_boundary"]["capture_mode"] == "loopback"


def test_public_mode_is_reported_so_a_reader_can_weigh_it(capture_port):
    """A public capture is a weaker observation and the artifact must say so."""
    token = "capture-token-value"
    report = _run(
        _gateway(capture_port, headers={"authorization": f"Bearer {token}"}),
        capture_port,
        capture_token=token,
        capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
    )
    assert report["capture"]["mode"] == "public"
    assert report["capture"]["authentication_required"] is True
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["capture_mode"] == "public"
    assert boundary["captured_requests"] == 1
    assert boundary["correlated_requests"] == 1
    assert report["passed"] is True, report["checks"]


def test_token_bearing_traffic_is_attributed_in_public_mode(capture_port):
    token = "capture-token-value"
    report = _run(
        _gateway(capture_port, headers={"x-conformance-capture-token": token}),
        capture_port,
        capture_token=token,
        capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
    )
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["captured_requests"] == 1
    assert boundary["unattributed_requests"] == 0


def test_untokened_traffic_is_reported_not_silently_dropped(capture_port):
    """A public capture WILL receive scan traffic and the report must show it."""
    token = "capture-token-value"
    report = _run(
        # The gateway forwards without the token: it is now unattributable, exactly as
        # an internet scanner's request would be.
        _gateway(capture_port),
        capture_port,
        capture_token=token,
        capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
    )
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["unattributed_requests"] >= 1
    assert boundary["captured_requests"] == 0
    assert boundary["unattributed_paths_observed"] == ["/v1/chat/completions"]
    # Not attributed, so it cannot correlate, so the boundary is a non-pass -- but the
    # traffic is on the record rather than discarded.
    assert boundary["passed"] is False


def test_unattributed_leak_evidence_fails_but_is_reported_separately(capture_port):
    """Fixture values reaching a public capture fail the check without asserting who sent them.

    The unattributed haystacks are inspected on their own and never joined into the
    target's channels: concatenating anonymous internet traffic into the cross-request
    joins would let a third party who knows the fixture manufacture a leak finding
    against the measured gateway.
    """
    token = "capture-token-value"

    class LeakingUntokenedGateway(_EchoGateway):
        capture_port = 0
        capture_headers: dict = {}

        def do_POST(self):  # noqa: N802
            import urllib.request

            # Built from the prompt this run actually carried. The fixture varies per
            # run, so the stranger being simulated here has to have seen it -- which
            # is precisely the threat the separate-haystack rule exists to defuse.
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            self.rfile = io.BytesIO(body)
            self.headers.replace_header("content-length", str(len(body)))
            prompt = json.loads(body)["messages"][-1]["content"]
            leaked = json.dumps(
                {"model": "m", "note": list(extract_fixture(prompt).values())}
            )
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.capture_port}/v1/chat/completions",
                data=leaked.encode(),
                headers={"content-type": "application/json"},
            )
            try:
                urllib.request.urlopen(request, timeout=30).read()
            except Exception:  # noqa: BLE001
                pass
            super().do_POST()

    handler = type(
        "Gateway",
        (LeakingUntokenedGateway,),
        {"capture_port": capture_port, "capture_headers": {}},
    )
    report = _run(
        handler,
        capture_port,
        capture_token=token,
        capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
    )
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["unattributed_leaked_entity_types"], boundary
    # Reported in its own field rather than attributed to the target.
    assert boundary["leaked_entity_types"] == []
    assert boundary["passed"] is False


def test_advertised_url_answered_by_another_server_aborts(capture_port):
    """Positive evidence that the target's configured address is hijacked."""
    other_port = _free_port()
    squatter = ThreadingHTTPServer(("127.0.0.1", other_port), _Squatter)
    threading.Thread(target=squatter.serve_forever, daemon=True).start()
    try:
        with pytest.raises(CaptureUnreachableError, match="advertised URL"):
            _run(
                _gateway(capture_port),
                capture_port,
                capture_token="token",
                capture_public_url=f"http://127.0.0.1:{other_port}/v1",
            )
    finally:
        squatter.shutdown()
        squatter.server_close()


def test_unreachable_advertised_url_is_recorded_rather_than_aborting(capture_port):
    """A valid tunnel address can be reachable from the target and not the harness.

    ``host.docker.internal`` resolves inside a container and often not on the host.
    Aborting there would reject a valid setup, so the limit is published instead --
    and a run that then captures nothing can be read as a reachability problem rather
    than as leak evidence.
    """
    report = _run(
        _gateway(capture_port),
        capture_port,
        capture_token="token",
        capture_public_url="http://host.invalid.example:9/v1",
    )
    probe = report["capture"]["self_probe"]
    assert probe["recorded"] is True
    assert probe["advertised_url_reachable"] is False
    assert probe["advertised_url_detail"].startswith("unreachable:")


def test_advertised_url_is_what_the_target_must_be_configured_for(capture_port):
    public = f"http://127.0.0.1:{capture_port}/v1"
    report = _run(
        _gateway(capture_port, headers={"authorization": "Bearer token"}),
        capture_port,
        capture_token="token",
        capture_public_url=public,
    )
    assert report["capture"]["target_must_be_preconfigured_for"] == public


def test_capture_token_is_never_published(capture_port):
    token = "super-secret-capture-token"
    report = _run(
        _gateway(capture_port, headers={"authorization": f"Bearer {token}"}),
        capture_port,
        capture_token=token,
        capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
    )
    assert token not in json.dumps(report)
