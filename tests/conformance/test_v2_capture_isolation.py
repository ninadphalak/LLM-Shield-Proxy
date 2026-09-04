"""The capture must not answer a case with a previous case's fixture.

An external-gateway run rebinds the capture to the SAME fixed port for every case, so a
gateway that pools connections can hold a keep-alive socket open across the case
boundary. If the predecessor's handler thread is still alive, it answers -- and the case
is scored against the WRONG injected values.

That failure mode is silent and it fails toward "secure": the needle for case N is absent
from a response that is really case N-1's, so the case scores as "not leaked". It was
found in a real Portkey run, where a passthrough gateway that redacts nothing scored a
LeakRate of 0.33.

This is the same family as the two capture-side false passes closed in the leak
inspector: a defect in the instrument that can only ever produce a flattering number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    UpstreamState,
    _make_upstream,
    _serve,
    _stop,
    build_segments,
)

PORT = 8798  # not 8799: a live external-gateway sweep may hold that one


def _post(url: str, prompt: str) -> str:
    body = json.dumps(
        {"model": "capture", "stream": True, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read().decode("utf-8", "replace")


def test_capture_refuses_connection_reuse() -> None:
    """A pooling client must not be able to keep a capture connection alive.

    `Connection: close` is what makes rebinding the port safe. Asserting on the header is
    asserting on the mechanism, so the test still fails if a refactor keeps per-case
    servers but drops the header.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    case = {
        "entity": "EMAIL",
        "encoding": "plain",
        "fragmentation": "single_chunk",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
    }
    state = UpstreamState(segments=segments, case=case)
    server, url = _serve(_make_upstream(state), port=PORT)
    try:
        body = json.dumps(
            {"model": "capture", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        with urlopen(  # noqa: S310
            Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=30
        ) as response:
            assert response.headers.get("Connection", "").lower() == "close"
    finally:
        _stop(server)


def test_rebound_capture_serves_the_new_fixture() -> None:
    """Two cases on one port: the second answer must carry the SECOND case's needle.

    Before `Connection: close` and `server_close()`, a client that reused the socket got
    case one's body back, and case two scored as a non-leak.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    prompt = "Please review: " + ", ".join(segments.echo.values())
    seen = []
    for entity in ("EMAIL", "SSN"):
        case = {
            "entity": entity,
            "encoding": "plain",
            "fragmentation": "single_chunk",
            "carrier": "sse-delta-content",
            "request_site": "chat-content",
        }
        state = UpstreamState(segments=segments, case=case)
        server, url = _serve(_make_upstream(state), port=PORT)
        try:
            seen.append(_post(url, prompt))
        finally:
            _stop(server)

    assert segments.injection["EMAIL"] in seen[0]
    assert segments.injection["SSN"] in seen[1], (
        "second case answered without its own needle -- a stale capture served it, and "
        "the case would have been scored as 'did not leak'"
    )
    assert segments.injection["EMAIL"] not in seen[1]


def test_stop_releases_the_port() -> None:
    """`_stop` must free the socket, or the next case silently binds nothing."""
    segments = build_segments("a1b2c3d4e5f60001")
    case = {
        "entity": "EMAIL",
        "encoding": "plain",
        "fragmentation": "single_chunk",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
    }
    first, _url = _serve(_make_upstream(UpstreamState(segments=segments, case=case)), port=PORT)
    _stop(first)
    try:
        second, _url2 = _serve(
            _make_upstream(UpstreamState(segments=segments, case=case)), port=PORT
        )
    except OSError as exc:  # pragma: no cover - the regression this guards
        pytest.fail(f"port {PORT} still bound after _stop: {exc}")
    _stop(second)


def test_a_response_that_never_reached_the_capture_is_inconclusive() -> None:
    """If the capture recorded nothing, the response came from somewhere else.

    A stale capture on the same port, a proxy cache, another process on the machine --
    the cause does not matter. What matters is that the client text belongs to a fixture
    this case never used, so its needles are absent and the case scores as "did not
    leak". A FALSE PASS, and the most flattering kind: it looks like a perfect row.

    Observed for real. A leftover capture from an earlier run held port 8799, the new
    capture bound alongside it (Windows SO_REUSEADDR lets a second socket bind, and the
    OLDER one keeps receiving), and a Higress probe reported every injected value
    contained while the capture for that run had recorded zero requests.
    """
    from pii_leak_benchmark.v2_emitter import _sse, run_case
    from http.server import BaseHTTPRequestHandler

    segments = build_segments("a1b2c3d4e5f60001")
    case = {
        "entity": "EMAIL",
        "encoding": "plain",
        "fragmentation": "single_chunk",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
    }

    class AnswersWithoutTheUpstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            body = _sse([{"content": "a response this run's capture never produced"}])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    gateway, url = _serve(AnswersWithoutTheUpstream, port=0)
    try:
        result = run_case(segments, "probe", case, iterations=1, gateway_url=url, upstream_port=0)
    finally:
        _stop(gateway)

    assert not result.upstream_bodies, "precondition: the capture must have seen nothing"
    assert result.transport_error, (
        "a case whose capture recorded no request was scored as a measurement; every "
        "needle is absent from a response this run did not produce, so it reads as a "
        "clean row"
    )
    assert not result.echo_observable
