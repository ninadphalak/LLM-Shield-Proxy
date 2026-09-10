"""Regression tests for the fifth adversarial review of the v2 instrument."""

from __future__ import annotations

import json
import socket
import sys
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark import v2_emitter as v2  # noqa: E402, I001


CASE = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "adversarial",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


def _result(**overrides) -> v2.RunResult:
    values = dict(
        policy="probe",
        case=dict(CASE),
        client_text="complete",
        echo_recovered={},
        echo_observable=True,
        transport_error=None,
        injection_leaked=False,
        events_observed=5,
        upstream_bodies=["{}"],
        latency_ms=[],
        data_events_observed=4,
        upstream_data_events=4,
        upstream_responses_observed=1,
        done_marker=True,
    )
    values.update(overrides)
    return v2.RunResult(**values)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_partial_capture_response_keeps_successful_frame_count() -> None:
    """A reset after frame N must record N, not erase the response."""
    segments = v2.build_segments("a1b2c3d4e5f60001")
    case = {**CASE, "fragmentation": "single_chunk"}
    state = v2.UpstreamState(segments=segments, case=case)
    handler = object.__new__(v2._make_upstream(state))
    body = json.dumps(v2.build_request(segments, case)).encode()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler.path = "/v1/chat/completions"
    handler.send_response = lambda *_args, **_kwargs: None
    handler.send_header = lambda *_args, **_kwargs: None
    handler.end_headers = lambda *_args, **_kwargs: None

    class ResetAfterThree:
        writes = 0

        def write(self, data: bytes) -> int:
            self.writes += 1
            if self.writes == 4:
                raise ConnectionResetError("scripted reset after three data frames")
            return len(data)

        def flush(self) -> None:
            return

    handler.wfile = ResetAfterThree()
    with pytest.raises(ConnectionResetError):
        handler._respond()

    assert len(state.response_records) == 1
    assert state.response_records[0].data_events_written == 3
    assert state.response_records[0].completed is False


def test_two_upstream_requests_are_not_paired_with_the_last_count() -> None:
    """Without protocol correlation, two upstream responses are incomparable."""
    upstream_port = _free_port()
    upstream_url = f"http://127.0.0.1:{upstream_port}"

    class TwiceGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            headers = {"Content-Type": "application/json", "Connection": "close"}
            with urlopen(Request(upstream_url, data=payload, headers=headers), timeout=10) as r:
                r.read()
            with pytest.raises(HTTPError):
                urlopen(Request(upstream_url, data=b"{", headers=headers), timeout=10).read()
            body = v2._sse_frames([{"content": "second response"}])[0] + v2.SSE_DONE_FRAME
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    gateway, gateway_url = v2._serve(TwiceGateway)
    try:
        segments = v2.build_segments("a1b2c3d4e5f60001")
        result = v2.run_case(
            segments,
            "twice",
            CASE,
            iterations=1,
            gateway_url=gateway_url,
            upstream_port=upstream_port,
        )
    finally:
        v2._stop(gateway)

    check = v2._fragmentation_check([result], segments)
    assert result.upstream_responses_observed == 2
    assert result.upstream_data_events == 4
    assert check["coalescing_rate"] is None
    assert check["coalescing_cases_compared"] == 0
    assert check["coalescing_per_case"][0]["upstream_responses_observed"] == 2


def test_transport_error_is_a_stream_failure_row() -> None:
    good = _result()
    dead = _result(
        transport_error="ReadTimeout: timed out",
        client_text="",
        data_events_observed=0,
    )
    check = v2._fragmentation_check(
        [good, dead], v2.build_segments("a1b2c3d4e5f60001")
    )

    assert len(check["coalescing_per_case"]) == 2
    assert check["stream_failure"] is True
    assert check["stream_failure_cases"] == 1
    assert check["coalescing_cases_compared"] == 1
    assert check["coalescing_per_case"][1]["coalesced"] is None


def test_later_missing_done_does_not_change_first_data_count(monkeypatch) -> None:
    """All-splits SSE validity must not be subtracted from first-split framing."""
    upstream_port = _free_port()
    upstream_url = f"http://127.0.0.1:{upstream_port}"
    calls = 0

    class LaterMissingDoneGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            nonlocal calls
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            request = Request(
                upstream_url,
                data=payload,
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            with urlopen(request, timeout=10) as response:
                response.read()
            calls += 1
            frames = v2._sse_frames([{"content": str(i)} for i in range(3)])
            body = b"".join(frames) + (v2.SSE_DONE_FRAME if calls == 1 else b"")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    # MIGRATED 2026-09-09 with the enumerator. Two two-part partitions, which is what the
    # original `[1, 2]` meant, expressed in the tuple form the three-part families need.
    monkeypatch.setattr(
        v2,
        "injection_partitions",
        lambda *_a, **_k: ([(1,), (2,)], ["exhaustive-2-part"] * 2,
                           {"exhaustive-2-part": 2}, {"exhaustive-2-part": False}),
    )
    gateway, gateway_url = v2._serve(LaterMissingDoneGateway)
    try:
        segments = v2.build_segments("a1b2c3d4e5f60001")
        result = v2.run_case(
            segments,
            "later-missing-done",
            CASE,
            iterations=1,
            gateway_url=gateway_url,
            upstream_port=upstream_port,
            oracle="exhaustive-2-part",
        )
    finally:
        v2._stop(gateway)

    assert result.done_marker is False
    assert result.events_observed == 4
    assert result.data_events_observed == 3
    assert result.upstream_data_events == 4
    assert v2._fragmentation_check([result], segments)["coalescing_rate"] == 1.0


def test_capture_producer_is_covered_by_inspector_digest(monkeypatch) -> None:
    before = v2.inspector_digest()
    original = v2._make_upstream

    def mutant(state):
        handler = original(state)
        real_respond = handler._respond

        def broken(self) -> None:
            real_respond(self)
            state.response_records[-1].data_events_written = 999

        handler._respond = broken
        return handler

    monkeypatch.setattr(v2, "_make_upstream", mutant)
    assert v2.inspector_digest() != before
