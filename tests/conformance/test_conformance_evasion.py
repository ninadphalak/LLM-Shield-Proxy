"""A gateway that leaks raw PII must never report passed: true.

Each gateway here redacts the VISIBLE request field - so the echo round-trips and
response fidelity passes - while exfiltrating protected values to the same configured
upstream through a representation or channel that preserves meaning. That is the
shape of a real leak: the audited field looks clean and a side channel carries the
payload. Some modes reproduce prior false negatives; the rest pin adjacent routes
and controlled mutations so the same blind spots cannot move channels.

The second half asserts the inverse: a conforming gateway must not be failed. The
correlation marker has already produced one false positive (a high-entropy nonce was
redacted by a conforming secret detector), and its replacement produced another (17
of 54 marker words were given names, which a conforming PERSON detector redacts).
"""

import base64
import gzip
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from llm_shield_proxy.conformance.http_profile import (
    _NONCE_MIN_MATCHES,
    _NONCE_WORD_COUNT,
    PROTECTED_VALUES,
    run_http_conformance,
)


def _free_port():
    """A fresh ephemeral port per test.

    A hardcoded capture port piles up TIME_WAIT sockets across a run, and on Windows
    SO_REUSEADDR lets a second binder take a port another process already holds - so
    two concurrent runs silently split the capture traffic and tests fail for a reason
    that has nothing to do with the code under test.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


RAW = list(PROTECTED_VALUES.values())
MASK = {value: f"[TOK_{index}]" for index, value in enumerate(RAW)}
BACKSLASH = chr(92)

# Given names that a conforming PERSON detector redacts. None may appear in the
# marker word list: redacting the marker must not look like a missing capture.
GIVEN_NAMES = frozenset(
    """amber april autumn basil bruno cedar clay cliff coral dawn dean ember fern flint
    forest gale hazel heath holly hunter iris ivory ivy jade jasmine jasper juniper lark
    laurel lily lilac maple mars meadow melody misty olive opal orchid pearl river robin
    rose rowan ruby sage scarlet sienna sky sterling summer terra violet willow wren""".split()
)


def _post(url, body, headers=None):
    request = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json", **(headers or {})}
    )
    try:
        return urllib.request.urlopen(request, timeout=30).read()
    except urllib.error.HTTPError as exc:
        return exc.read()


def _raw_request(port, request):
    """Send an exact HTTP/1.x request and return its response body."""
    with socket.create_connection(("127.0.0.1", port), timeout=30) as connection:
        connection.settimeout(30)
        connection.sendall(request)
        response = bytearray()
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
    return bytes(response).partition(b"\r\n\r\n")[2]


def _chunked_post(port, body, *, extension="", trailers=None):
    trailers = trailers or {}
    trailer_declaration = ""
    if trailers:
        trailer_declaration = "Trailer: " + ", ".join(trailers) + "\r\n"
    trailer_block = "".join(f"{name}: {value}\r\n" for name, value in trailers.items())
    request = (
        "POST /v1/chat/completions HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Transfer-Encoding: chunked\r\n"
        f"{trailer_declaration}"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    request += f"{len(body):X}{extension}\r\n".encode("ascii") + body + b"\r\n"
    request += b"0\r\n" + trailer_block.encode("ascii") + b"\r\n"
    return _raw_request(port, request)


def _hide(mode, values):
    """Return the raw values in a form that survives a JSON round trip to upstream."""
    if mode in ("plain", "gzip"):
        # gzip disguises the wire bytes, not the parsed structure.
        return values
    if mode == "deep_nesting":
        node = {"pii": values}
        for _ in range(40):
            node = {"n": node}
        return node
    if mode == "numeric_leaves":
        # Bare JSON numbers, no strings involved anywhere in the payload.
        return [int("".join(c for c in value if c.isdigit())) for value in values[1:]]
    if mode == "char_codes":
        return [[ord(character) for character in value] for value in values]
    if mode == "prefixed_base64":
        return ["x" + base64.b64encode(v.encode()).decode() for v in values]
    if mode == "double_base64":
        return [base64.b64encode(base64.b64encode(v.encode())).decode() for v in values]
    if mode == "hex":
        return [value.encode().hex() for value in values]
    if mode == "percent_encoded":
        return ["%" + "%".join(f"{byte:02x}" for byte in value.encode()) for value in values]
    if mode == "case_variant_email":
        # Email local/domain matching is case-insensitive for this conformance
        # target. The normalized oracle must not depend on source casing.
        return [values[0].upper()]
    if mode == "key_interleaved_fragments":
        # Fragments separated by dict KEYS: a check that joins every collected string
        # in document order splices the keys in between and misses the value.
        out = {}
        for index, value in enumerate(values):
            middle = len(value) // 2
            out[f"k{index}a"], out[f"k{index}b"] = value[:middle], value[middle:]
        return out
    if mode == "unicode_escaped":
        return [
            json.loads(
                '"' + "".join(BACKSLASH + "u%04x" % ord(c) if c in "-@." else c for c in value) + '"'
            )
            for value in values
        ]
    raise AssertionError(f"unknown mode {mode}")


def _leaking_gateway(mode, capture_port):
    upstream = f"http://127.0.0.1:{capture_port}/v1/chat/completions"

    class LeakingGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked = prompt
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)

            body = {"model": "m", "messages": [{"role": "user", "content": masked}]}
            headers = {}
            if mode == "header_channel":
                # Metadata headers reach the upstream exactly as the body does. A
                # gateway can hand over a clean message field and carry the raw
                # values alongside it, so an unwalked header is an unwatched channel.
                headers.update(
                    {f"x-orig-{index}": value for index, value in enumerate(RAW)}
                )
            elif mode == "sibling_endpoint":
                # Same upstream origin, different route. The boundary under test is
                # the origin, not one path.
                _post(f"http://127.0.0.1:{capture_port}/v1/embeddings", json.dumps({"input": RAW}).encode())
            elif mode == "query_string":
                _post(
                    f"http://127.0.0.1:{capture_port}/v1/chat/completions?trace={RAW[1]}",
                    json.dumps(body).encode(),
                )
            elif mode == "unsupported_method_channel":
                # BaseHTTPRequestHandler parses this request, but a handler that only
                # defines a fixed verb list emits 501 before the capture hook runs.
                raw_headers = "".join(
                    f"x-orig-{index}: {value}\r\n" for index, value in enumerate(RAW)
                )
                _raw_request(
                    capture_port,
                    (
                        "HEAD /v1/telemetry HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{capture_port}\r\n"
                        f"{raw_headers}"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii"),
                )
            elif mode == "cross_request_fragments":
                # A stateful upstream can reassemble a field carried over ordered
                # requests. No individual request contains a protected value.
                for value in RAW:
                    middle = len(value) // 2
                    for part in (value[:middle], value[middle:]):
                        fragment = json.dumps({"piece": part}).encode()
                        _raw_request(
                            capture_port,
                            (
                                "POST /v1/fragments HTTP/1.0\r\n"
                                f"Content-Length: {len(fragment)}\r\n"
                                "Content-Type: application/json\r\n\r\n"
                            ).encode("ascii")
                            + fragment,
                        )
            elif mode == "parser_recursion":
                # Parser/materialization recursion must not run before the bounded
                # object walk leaves a fail-closed capture record. A later clean
                # request is only a decoy and cannot erase the deep request.
                deep = b'{"nested":' * 2_000 + json.dumps(RAW).encode() + b"}" * 2_000
                _raw_request(
                    capture_port,
                    (
                        "POST /v1/deep HTTP/1.0\r\n"
                        f"Content-Length: {len(deep)}\r\n"
                        "Content-Type: application/json\r\n\r\n"
                    ).encode("ascii")
                    + deep,
                )
            elif mode == "cross_request_header_fragments":
                for value in RAW:
                    middle = len(value) // 2
                    first, second = value[:middle], value[middle:]
                    for fragment, first_header in ((first, False), (second, True)):
                        ordered_headers = (
                            f"X-Piece: {fragment}\r\nHost: 127.0.0.1:{capture_port}\r\n"
                            "Content-Length: 2\r\nConnection: close\r\n"
                            if first_header
                            else f"Host: 127.0.0.1:{capture_port}\r\n"
                            "Content-Length: 2\r\nConnection: close\r\n"
                            f"X-Piece: {fragment}\r\n"
                        )
                        _raw_request(
                            capture_port,
                            (
                                "POST /v1/fragments HTTP/1.0\r\n"
                                + ordered_headers
                                + "\r\n{}"
                            ).encode("ascii"),
                        )
            elif mode == "cross_request_framing_fragments":
                for value in RAW:
                    middle = len(value) // 2
                    for fragment in (value[:middle], value[middle:]):
                        _chunked_post(
                            capture_port,
                            b"{}",
                            extension=";piece=" + urllib.parse.quote(fragment, safe=""),
                        )
            elif mode == "cross_request_request_line_fragments":
                for value in RAW:
                    middle = len(value) // 2
                    first, second = value[:middle], value[middle:]
                    _raw_request(
                        capture_port,
                        (
                            "POST /v1/fragments?piece="
                            + urllib.parse.quote(first, safe="")
                            + " HTTP/1.0\r\nConnection: close\r\n\r\n"
                        ).encode("ascii"),
                    )
                    _raw_request(
                        capture_port,
                        (
                            f"{second} /v1/fragments HTTP/1.0\r\n"
                            "Connection: close\r\n\r\n"
                        ).encode("ascii"),
                    )
            elif mode not in (
                "trailer_channel",
                "chunk_extension_channel",
                "duplicate_json_members",
                "malformed_header_line",
                "malformed_bodyless_header_line",
                "parser_recursion",
                "cross_request_header_fragments",
                "cross_request_framing_fragments",
                "cross_request_request_line_fragments",
            ):
                body["telemetry"] = _hide(mode, RAW)
            payload = json.dumps(body).encode()
            if mode == "trailer_channel":
                response = _chunked_post(
                    capture_port,
                    payload,
                    trailers={f"x-orig-{index}": value for index, value in enumerate(RAW)},
                )
            elif mode == "chunk_extension_channel":
                extension = "".join(
                    f';x-orig-{index}="{value}"' for index, value in enumerate(RAW)
                )
                response = _chunked_post(capture_port, payload, extension=extension)
            elif mode == "duplicate_json_members":
                # A normal JSON object walk sees only the last value for a duplicate
                # member. The upstream still received the earlier member bytes, and
                # first-wins parsers can expose the protected values directly.
                payload = (
                    '{"telemetry":'
                    + json.dumps(RAW)
                    + ',"telemetry":"[REDACTED]","model":"m","messages":'
                    + json.dumps(body["messages"])
                    + "}"
                ).encode()
                response = _post(upstream, payload, headers)
            elif mode == "malformed_header_line":
                # Python's header parser can retain the valid framing fields while
                # discarding a malformed later line. Those bytes nevertheless reached
                # the capture origin and must make the request uninspectable or leaked.
                malformed = "X Orig: " + " | ".join(RAW)
                response = _raw_request(
                    capture_port,
                    (
                        "POST /v1/chat/completions HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{capture_port}\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(payload)}\r\n"
                        "Connection: close\r\n"
                        f"{malformed}\r\n\r\n"
                    ).encode("ascii")
                    + payload,
                )
            elif mode == "malformed_bodyless_header_line":
                malformed = "X Orig: " + " | ".join(RAW)
                _raw_request(
                    capture_port,
                    (
                        "POST /v1/fragments HTTP/1.0\r\n"
                        "Connection: close\r\n"
                        f"{malformed}\r\n\r\n"
                    ).encode("ascii"),
                )
                response = _post(upstream, payload, headers)
            else:
                if mode == "gzip":
                    payload, headers["content-encoding"] = gzip.compress(payload), "gzip"
                response = _post(upstream, payload, headers)

            text = "".join(
                json.loads(line[5:])["choices"][0]["delta"]["content"]
                for line in response.decode().splitlines()
                if line.startswith("data:") and line[5:].strip() != "[DONE]"
            )
            for raw, token in MASK.items():
                text = text.replace(token, raw)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in text:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return LeakingGateway


def _run(handler_class, port, iterations=1):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=iterations,
            capture_port=port,
        )
    finally:
        server.shutdown()
        server.server_close()


LEAK_MODES = [
    "plain",
    "gzip",
    "unicode_escaped",
    "deep_nesting",
    "char_codes",
    "numeric_leaves",
    "prefixed_base64",
    "double_base64",
    "hex",
    "percent_encoded",
    "case_variant_email",
    "key_interleaved_fragments",
    "sibling_endpoint",
    "query_string",
    "header_channel",
    "trailer_channel",
    "chunk_extension_channel",
    "unsupported_method_channel",
    "cross_request_fragments",
    "duplicate_json_members",
    "malformed_header_line",
    "malformed_bodyless_header_line",
    "parser_recursion",
    "cross_request_header_fragments",
    "cross_request_framing_fragments",
    "cross_request_request_line_fragments",
]


@pytest.mark.parametrize("mode", LEAK_MODES)
def test_leaking_gateway_never_passes(mode, capture_port):
    report = _run(_leaking_gateway(mode, capture_port), capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert report["passed"] is False, f"{mode} evaded the harness"
    assert boundary["passed"] is False
    assert boundary["leaked_entity_types"] or boundary["uninspectable_requests"], (
        f"{mode} leaked without detection or a fail-closed record"
    )


@pytest.mark.parametrize("mode", ["plain", "char_codes", "hex", "prefixed_base64"])
def test_every_protected_entity_is_recovered(mode, capture_port):
    """Detecting one entity fails the check, but a decoder that only ever recovers
    the shortest value is a decoder that mostly does not work."""
    report = _run(_leaking_gateway(mode, capture_port), capture_port)
    leaked = report["checks"]["configured_upstream_boundary"]["leaked_entity_types"]
    assert sorted(leaked) == sorted(PROTECTED_VALUES), f"{mode} recovered only {leaked}"


def test_fragment_split_inside_a_protected_value_is_reassembled(capture_port):
    """Bisect a specific value, not the prompt midpoint.

    Splitting the prompt in half happened to leave the SSN and card intact in one
    half, so this case passed on the per-string check and never exercised fragment
    reassembly at all - the joined-fragment check could be deleted outright with
    every test still green.
    """
    email = PROTECTED_VALUES["EMAIL"]
    middle = len(email) // 2
    parts = [email[:middle], email[middle:]]
    assert not any(value in part for part in parts for value in PROTECTED_VALUES.values())

    def _fragment_gateway(port):
      class FragmentGateway(BaseHTTPRequestHandler):
          protocol_version = "HTTP/1.1"

          def log_message(self, *_args):
              return

          def do_POST(self):  # noqa: N802
              length = int(self.headers.get("content-length", "0"))
              prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
              masked = prompt
              for raw, token in MASK.items():
                  masked = masked.replace(raw, token)
              _post(
                  f"http://127.0.0.1:{port}/v1/chat/completions",
                  json.dumps(
                      {"model": "m", "messages": [{"role": "user", "content": masked}], "parts": parts}
                  ).encode(),
              )
              self.send_response(200)
              self.send_header("content-type", "text/event-stream")
              self.send_header("connection", "close")
              self.end_headers()
              for character in prompt:
                  event = {"choices": [{"delta": {"content": character}}]}
                  self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
              self.wfile.write(b"data: [DONE]\n\n")
              self.wfile.flush()
      return FragmentGateway

    report = _run(_fragment_gateway(capture_port), capture_port)
    assert report["checks"]["configured_upstream_boundary"]["leaked_entity_types"] == ["EMAIL"]
    assert report["passed"] is False


def test_body_too_deep_to_inspect_fails_closed(capture_port):
    """An inspection that ran out of budget has not shown the body to be clean."""

    def _too_deep_gateway(port):
      class TooDeepGateway(BaseHTTPRequestHandler):
          protocol_version = "HTTP/1.1"

          def log_message(self, *_args):
              return

          def do_POST(self):  # noqa: N802
              length = int(self.headers.get("content-length", "0"))
              prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
              node = {"leaf": "nothing to see"}
              for _ in range(400):
                  node = {"n": node}
              _post(
                  f"http://127.0.0.1:{port}/v1/chat/completions",
                  json.dumps(
                      {"model": "m", "messages": [{"role": "user", "content": prompt}], "deep": node}
                  ).encode(),
              )
              self.send_response(200)
              self.send_header("content-type", "text/event-stream")
              self.send_header("connection", "close")
              self.end_headers()
              for character in prompt:
                  event = {"choices": [{"delta": {"content": character}}]}
                  self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
              self.wfile.write(b"data: [DONE]\n\n")
              self.wfile.flush()
      return TooDeepGateway

    boundary = _run(_too_deep_gateway(capture_port), capture_port)["checks"]["configured_upstream_boundary"]
    assert boundary["uninspectable_requests"] >= 1
    assert boundary["passed"] is False
    assert "inspection_truncated" in boundary["uninspectable_reasons"]


def test_uncorrelated_traffic_does_not_satisfy_the_boundary_check(capture_port):
    """A capture the harness cannot tie to this run is not evidence the target behaved.

    Without correlation a target can exfiltrate raw PII to its real upstream and
    satisfy the boundary check with one unrelated request to the capture server.
    """

    def _decoy_gateway(port):
      class DecoyGateway(BaseHTTPRequestHandler):
          protocol_version = "HTTP/1.1"

          def log_message(self, *_args):
              return

          def do_POST(self):  # noqa: N802
              length = int(self.headers.get("content-length", "0"))
              text = json.loads(self.rfile.read(length))["messages"][-1]["content"]
              _post(
                  f"http://127.0.0.1:{port}/v1/chat/completions",
                  json.dumps(
                      {"model": "m", "messages": [{"role": "user", "content": "unrelated filler"}]}
                  ).encode(),
              )
              self.send_response(200)
              self.send_header("content-type", "text/event-stream")
              self.send_header("connection", "close")
              self.end_headers()
              for character in text:
                  event = {"choices": [{"delta": {"content": character}}]}
                  self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
              self.wfile.write(b"data: [DONE]\n\n")
              self.wfile.flush()
      return DecoyGateway

    report = _run(_decoy_gateway(capture_port), capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["captured_requests"] >= 1
    assert boundary["correlated_requests"] == 0
    assert boundary["passed"] is False
    assert report["passed"] is False


def test_raw_pass_through_baseline_is_recorded_as_failing(capture_port):
    """The negative control must fail on measured evidence, not on its label."""
    report = run_http_conformance("capture://self", iterations=1, capture_port=capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert report["passed"] is False
    assert sorted(boundary["leaked_entity_types"]) == ["CREDIT_CARD", "EMAIL", "SSN"]


# --- the inverse: conforming gateways the harness must not fail ---------------------


def _conforming_gateway(port, redact_words=frozenset(), extra_payload=None, single_event=False):
    """Redacts every protected value, forwards nothing raw, rehydrates the response."""

    class ConformingGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked, restored = prompt, {}
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)
            for index, word in enumerate(sorted(redact_words)):
                if word in masked:
                    token = f"[PERSON_{index}]"
                    restored[token] = word
                    masked = masked.replace(word, token)
            upstream_payload = {"model": "m", "messages": [{"role": "user", "content": masked}]}
            if extra_payload is not None:
                upstream_payload["metadata"] = extra_payload
            response = _post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json.dumps(upstream_payload).encode(),
            )
            text = "".join(
                json.loads(line[5:])["choices"][0]["delta"]["content"]
                for line in response.decode().splitlines()
                if line.startswith("data:") and line[5:].strip() != "[DONE]"
            )
            for token, word in restored.items():
                text = text.replace(token, word)
            for raw, token in MASK.items():
                text = text.replace(token, raw)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            response_fragments = [text] if single_event else text
            for fragment in response_fragments:
                event = {"choices": [{"delta": {"content": fragment}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return ConformingGateway


def test_conforming_gateway_passes(capture_port):
    report = _run(_conforming_gateway(capture_port), capture_port, iterations=2)
    assert report["passed"] is True, report["checks"]


def test_conforming_gateway_may_make_a_bodyless_auxiliary_request(capture_port):
    """No Content-Length means a zero-length request body under HTTP/1.1."""

    class BodylessAuxiliaryGateway(_conforming_gateway(capture_port)):
        def do_POST(self):  # noqa: N802
            _raw_request(
                capture_port,
                (
                    "GET /v1/models HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{capture_port}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii"),
            )
            super().do_POST()

    report = _run(BodylessAuxiliaryGateway, capture_port)
    assert report["passed"] is True, report["checks"]


def test_single_event_response_does_not_satisfy_fragmentation(capture_port):
    report = _run(_conforming_gateway(capture_port, single_event=True), capture_port)
    fragmentation = report["checks"]["fragmentation_safety"]
    assert fragmentation["events_observed"] == 1
    assert fragmentation["passed"] is False
    assert report["passed"] is False


def test_generated_marker_words_are_distinct(monkeypatch):
    """Duplicate draws can leave fewer distinct words than correlation requires."""
    from llm_shield_proxy.conformance import http_profile as conformance_http

    monkeypatch.setattr(conformance_http.secrets, "choice", lambda words: words[0])
    monkeypatch.setattr(conformance_http.secrets, "randbelow", lambda _limit: 0, raising=False)
    marker = conformance_http._make_nonce().split("-")
    assert len(marker) == _NONCE_WORD_COUNT
    assert len(set(marker)) == _NONCE_WORD_COUNT


def test_duplicate_marker_draw_cannot_fail_a_conforming_gateway(capture_port, monkeypatch):
    from llm_shield_proxy.conformance import http_profile as conformance_http

    # Before draws were made without replacement, this possible run contained only
    # two distinct words and could never meet the three-word correlation threshold.
    monkeypatch.setattr(conformance_http.secrets, "choice", lambda words: words[0])
    monkeypatch.setattr(conformance_http.secrets, "randbelow", lambda _limit: 0)
    report = _run(_conforming_gateway(capture_port), capture_port)
    assert report["passed"] is True, report["checks"]


def test_every_iteration_contributes_to_response_fidelity(capture_port):
    """A good last response must not erase an earlier wrong reconstruction."""

    class LastOnlyGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        requests = 0

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked = prompt
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)
            _post(
                f"http://127.0.0.1:{capture_port}/v1/chat/completions",
                json.dumps({"model": "m", "messages": [{"role": "user", "content": masked}]}).encode(),
            )
            type(self).requests += 1
            text = "wrong-but-still-fragmented" if self.requests == 1 else prompt
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in text:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    report = _run(LastOnlyGateway, capture_port, iterations=2)
    fidelity = report["checks"]["response_fidelity"]
    assert fidelity["iterations_matching"] == 1
    assert fidelity["passed"] is False
    assert report["passed"] is False


def test_sessions_are_namespaced_across_profile_invocations(capture_port):
    """A compliant loop breaker must not mistake separate profile runs for one loop."""
    active_capture = {"port": capture_port}

    class StatefulConformingGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        seen_sessions = set()

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            session_id = self.headers.get("x-session-id")
            if session_id in self.seen_sessions:
                payload = b'{"error":"agent_loop_detected"}'
                self.send_response(429)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.send_header("connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.seen_sessions.add(session_id)

            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked = prompt
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)
            _post(
                f"http://127.0.0.1:{active_capture['port']}/v1/chat/completions",
                json.dumps({"model": "m", "messages": [{"role": "user", "content": masked}]}).encode(),
            )
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatefulConformingGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    reports = []
    try:
        for port in (capture_port, _free_port()):
            active_capture["port"] = port
            reports.append(
                run_http_conformance(
                    f"http://127.0.0.1:{server.server_address[1]}/v1",
                    iterations=2,
                    capture_port=port,
                )
            )
    finally:
        server.shutdown()
        server.server_close()

    assert all(report["passed"] for report in reports), [report["checks"] for report in reports]
    assert len(StatefulConformingGateway.seen_sessions) == 4


def test_unsupported_http_transport_fails_closed(capture_port):
    """A parser rejection must be evidence of a blind spot, not an invisible request."""

    class UnsupportedProtocolGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked = prompt
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)
            raw_headers = "".join(
                f"x-orig-{index}: {value}\r\n" for index, value in enumerate(RAW)
            )
            _raw_request(
                capture_port,
                (
                    "POST /v1/transport HTTP/2.0\r\n"
                    f"{raw_headers}\r\n"
                ).encode("ascii"),
            )
            _post(
                f"http://127.0.0.1:{capture_port}/v1/chat/completions",
                json.dumps({"model": "m", "messages": [{"role": "user", "content": masked}]}).encode(),
            )
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    report = _run(UnsupportedProtocolGateway, capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert report["passed"] is False
    assert boundary["uninspectable_requests"] >= 1
    assert "http_protocol_error_505" in boundary["uninspectable_reasons"]


def test_clean_request_over_inspection_budget_is_an_explicit_non_pass(capture_port):
    """Fail-closed is intentional, but must be distinguishable from a detected leak."""
    benign = {"leaf": "ordinary metadata"}
    for _ in range(80):
        benign = {"nested": benign}

    report = _run(_conforming_gateway(capture_port, extra_payload=benign), capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert report["passed"] is False
    assert boundary["leaked_entity_types"] == []
    assert boundary["uninspectable_requests"] >= 1
    assert any("failed closed as uninspectable" in item for item in report["limitations"])


def test_egress_deferred_until_after_capture_shutdown_is_disclosed(capture_port):
    """A finite external observation cannot certify behavior after it has stopped."""
    attempted = threading.Event()
    timers = []

    class DelayedGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            masked = prompt
            for raw, token in MASK.items():
                masked = masked.replace(raw, token)
            _post(
                f"http://127.0.0.1:{capture_port}/v1/chat/completions",
                json.dumps({"model": "m", "messages": [{"role": "user", "content": masked}]}).encode(),
            )

            def delayed_leak():
                try:
                    payload = json.dumps({"raw": RAW}).encode()
                    with socket.create_connection(("127.0.0.1", capture_port), timeout=1) as connection:
                        connection.settimeout(1)
                        connection.sendall(
                            (
                                "POST /v1/telemetry HTTP/1.0\r\n"
                                f"Content-Length: {len(payload)}\r\n"
                                "Content-Type: application/json\r\n\r\n"
                            ).encode("ascii")
                            + payload
                        )
                except Exception:
                    pass
                finally:
                    attempted.set()

            timer = threading.Timer(2.0, delayed_leak)
            timer.daemon = True
            timers.append(timer)
            timer.start()

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    report = _run(DelayedGateway, capture_port)
    for timer in timers:
        timer.join(timeout=3)
    assert attempted.is_set()
    assert report["passed"] is True
    assert any("observation window ends" in item for item in report["limitations"])
    assert any("request counts" in item for item in report["limitations"])


def test_marker_words_are_not_given_names():
    """A marker word a conforming PERSON detector redacts is a false-positive source."""
    from llm_shield_proxy.conformance.http_profile import _NONCE_WORDS

    collisions = sorted(GIVEN_NAMES.intersection(_NONCE_WORDS))
    assert not collisions, f"marker words a name detector would redact: {collisions}"


def test_correlation_survives_partial_marker_redaction(capture_port):
    """A conforming detector may redact part of the marker; a majority still correlates."""
    redactable = _NONCE_WORD_COUNT - _NONCE_MIN_MATCHES
    assert redactable >= 1, "the marker must tolerate at least one redacted word"

    from llm_shield_proxy.conformance import http_profile as conformance_http

    seen = {}
    original = conformance_http._build_prompt

    def capture_prompt(nonce):
        seen["words"] = nonce.split("-")
        return original(nonce)

    conformance_http._build_prompt = capture_prompt
    try:
        # Probe once to learn this run's marker, then redact the tolerated number.
        probe = _run(_conforming_gateway(capture_port), capture_port, iterations=1)
        assert probe["passed"] is True
        words = frozenset(seen["words"][:redactable])
        report = _run(_conforming_gateway(capture_port, redact_words=words), capture_port, iterations=1)
    finally:
        conformance_http._build_prompt = original

    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["correlated_requests"] >= 1, f"redacting {sorted(words)} broke correlation"
    assert report["passed"] is True, report["checks"]
