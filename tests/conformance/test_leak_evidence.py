"""`leaked_entity_types` is the one claim this harness cannot retract.

Until now it was a bare assertion: an entity name, no channel, no matcher, no way for
the accused to see what produced it. Public capture mode made that worse, because the
path between the target and the capture injects headers of its own and those are
inspected as an egress channel (correctly -- a gateway could hide values there).

Measured through a real Cloudflare quick tunnel: ten headers added, none removed, body
byte-identical, content-length framing preserved, and the request goes from 20 digits
to 93. Needle proximity was unchanged (SSN 2 of 9 direct and tunnelled), so the margin
did not actually erode -- but exactly one valid IPv4 address, 123.45.67.89, normalizes
to the same digits as the SSN fixture, and a client connecting from it makes a
perfectly-redacting gateway report `leaked: ["SSN"]`.

IPv4 is not the only vector: any injected identifier whose digits happen to contain a
protected value collides. The first draft of the tunnel-header fixture below invented a
cf-warp-tag-id ending "...ef0123456789" and failed this file's own honest-gateway test,
which is the cheapest possible demonstration of the point.

That is not suppressed. Dropping header inspection to remove a rare event would open a
real evasion channel. Instead the artifact publishes which matcher fired, so a
normalized cross-request match is distinguishable from a verbatim value in the body.
"""

import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from llm_shield_proxy.conformance.http_profile import (
    PROTECTED_VALUES,
    _needle_proximity,
    _normalize,
    run_http_conformance,
)

MASK = {v: f"[TOK_{i}]" for i, v in enumerate(PROTECTED_VALUES.values())}
# What cloudflared actually adds, measured against a live quick tunnel.
TUNNEL_HEADERS = {
    "cf-ray": "9a2b3c4d5e6f7890-LHR",
    "cf-ipcountry": "GB",
    "cf-visitor": '{"scheme":"https"}',
    "cf-warp-tag-id": "b7d41e2a-9c3f-4a86-be55-7f0da2c9e418",
    "cf-ew-via": "15",
    "cf-worker": "trycloudflare.com",
    "cdn-loop": "cloudflare; loops=1",
    "x-forwarded-proto": "https",
}


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


def _gateway(port, *, redact=True, extra_headers=None):
    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
            prompt = payload["messages"][-1]["content"]
            forwarded = prompt
            if redact:
                for raw, token in MASK.items():
                    forwarded = forwarded.replace(raw, token)
            payload["messages"][-1]["content"] = forwarded
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=json.dumps(payload).encode(),
                        headers={"content-type": "application/json", **(extra_headers or {})},
                    ),
                    timeout=30,
                ).read()
            except Exception:  # noqa: BLE001
                pass
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                self.wfile.write(
                    b"data: "
                    + json.dumps({"choices": [{"delta": {"content": character}}]}).encode()
                    + b"\n\n"
                )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Gateway


def _run(port, **kwargs):
    handler = _gateway(port, **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=2,
            capture_port=port,
        )
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------- the metric itself


def test_needle_proximity_is_the_needle_run_not_a_digit_count():
    """The first version of this metric measured the longest digit run.

    Traced against the live proxy that is 16 on a CLEAN run -- the gateway's own
    synthetic replacement card. A correctly-redacting gateway emits substitutes shaped
    like the fixture, so a digit-run count is dominated by them and says nothing about
    headroom. This metric measures the needle instead.
    """
    needle = _normalize(PROTECTED_VALUES["CREDIT_CARD"])  # 16 digits
    # A different 16-digit card: plenty of digits, but almost none of THIS needle.
    substitute = _normalize("4187-4907-6848-0972")
    assert len(substitute) == 16
    assert _needle_proximity(needle, (substitute,)) < len(needle)
    # The needle itself is a full-length match.
    assert _needle_proximity(needle, (needle,)) == len(needle)


def test_proximity_equals_needle_length_exactly_when_the_value_is_present(capture_port):
    leaking = _run(capture_port, redact=False)
    boundary = leaking["checks"]["configured_upstream_boundary"]
    for entity in ("EMAIL", "SSN", "CREDIT_CARD"):
        assert boundary["needle_proximity"][entity] == boundary["needle_lengths"][entity]


def test_honest_run_keeps_a_margin(capture_port):
    """Round 6 measured the SSN margin by hand at 2 of 9. Now it is published."""
    report = _run(capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["leaked_entity_types"] == []
    for entity in PROTECTED_VALUES:
        assert boundary["needle_proximity"][entity] < boundary["needle_lengths"][entity]


# ------------------------------------------------------------------ attribution


def test_a_verbatim_leak_is_reported_as_a_literal_match(capture_port):
    report = _run(capture_port, redact=False)
    evidence = report["checks"]["configured_upstream_boundary"]["leak_evidence"]
    assert evidence, "a leak must publish its evidence"
    by_entity = {item["entity_type"]: item for item in evidence}
    for entity in ("EMAIL", "SSN", "CREDIT_CARD"):
        assert by_entity[entity]["match"] == "literal"
        assert by_entity[entity]["channel"]
        assert by_entity[entity]["scope"]


def test_a_clean_run_publishes_no_evidence(capture_port):
    report = _run(capture_port)
    assert report["checks"]["configured_upstream_boundary"]["leak_evidence"] == []


def test_tunnel_injected_headers_do_not_fail_an_honest_gateway(capture_port):
    """The measured cloudflared header set, minus the client-IP collision case."""
    report = _run(capture_port, extra_headers={**TUNNEL_HEADERS, "x-forwarded-for": "203.0.113.45"})
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["leaked_entity_types"] == []
    assert boundary["uninspectable_requests"] == 0
    assert report["passed"] is True, report["checks"]


def test_the_client_ip_collision_is_distinguishable_from_a_real_leak(capture_port):
    """The enumerated false positive, and the field that lets a reader catch it.

    123.45.67.89 is the ONLY valid IPv4 that normalizes to the SSN fixture's digits.
    A client connecting from it makes a perfectly-redacting gateway report an SSN
    finding. That is not suppressed -- dropping header inspection would open a real
    evasion channel -- so what must hold is that the artifact says the match was
    NORMALIZED, not literal, and a reader can tell it from a value sent verbatim.
    """
    collision = _run(
        capture_port,
        extra_headers={**TUNNEL_HEADERS, "x-forwarded-for": "123.45.67.89"},
    )
    boundary = collision["checks"]["configured_upstream_boundary"]
    assert boundary["leaked_entity_types"] == ["SSN"], "the enumerated collision changed"
    evidence = {item["entity_type"]: item for item in boundary["leak_evidence"]}
    assert evidence["SSN"]["match"] == "normalized"

    # A genuine verbatim leak is reported differently, which is the whole point.
    real = _run(capture_port, redact=False)
    real_evidence = {
        item["entity_type"]: item
        for item in real["checks"]["configured_upstream_boundary"]["leak_evidence"]
    }
    assert real_evidence["SSN"]["match"] == "literal"
    assert evidence["SSN"]["match"] != real_evidence["SSN"]["match"]


def test_the_card_needle_cannot_collide_with_any_ipv4():
    """16 normalized digits; the longest possible IPv4 digit string is 12."""
    assert len(_normalize(PROTECTED_VALUES["CREDIT_CARD"])) == 16
    longest_possible_ipv4_digits = len("255255255255")
    assert longest_possible_ipv4_digits < len(_normalize(PROTECTED_VALUES["CREDIT_CARD"]))


def test_exactly_one_ipv4_collides_with_the_ssn_needle():
    """Pins the enumeration the limitation text states. If the fixture changes, this
    fails and the documented claim must be re-derived rather than silently going stale.
    """
    needle = _normalize(PROTECTED_VALUES["SSN"])
    found = []
    for a in range(1, 4):
        for b in range(1, 4):
            for c in range(1, 4):
                d = len(needle) - a - b - c
                if not 1 <= d <= 3:
                    continue
                parts = [needle[:a], needle[a:a + b], needle[a + b:a + b + c], needle[a + b + c:]]
                if any(len(part) > 1 and part[0] == "0" for part in parts):
                    continue
                if all(int(part) <= 255 for part in parts):
                    found.append(".".join(parts))
    assert found == ["123.45.67.89"], found


def test_limitations_disclose_the_matcher_and_the_collision(capture_port):
    report = _run(capture_port)
    run_validity = report["limitations"]["run_validity"]
    assert any("leak_evidence" in item for item in run_validity)
    assert any("123.45.67.89" in item for item in run_validity)
