"""Endpoint-neutral HTTP profile for streaming privacy gateway evaluations."""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import json
import os
import platform
import re
import secrets
import statistics
import sys
import threading
import time
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import unquote_plus, urljoin

import httpx

PROTECTED_VALUES = {
    "EMAIL": "person@example.invalid",
    "SSN": "123-45-6789",
    "CREDIT_CARD": "4532-1234-5678-9012",
}
_PROMPT_TEMPLATE = (
    "Synthetic conformance record {nonce}: contact person@example.invalid, "
    "SSN 123-45-6789, card 4532-1234-5678-9012"
)
# Retained for callers and tests that need a stable example of the fixture shape.
PROMPT = _PROMPT_TEMPLATE.format(nonce="bracket-gravel-kettle-pantry-socket")

# Bounds on what the capture server will buffer and inspect from one request.
# Exceeding any of them marks the capture uninspectable, which FAILS the boundary
# check. A budget that silently stopped the walk would let a target hide the PII
# below the limit and read as clean.
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_INSPECT_DEPTH = 64
_MAX_INSPECT_NODES = 200_000
_MAX_DECODE_ROUNDS = 3
_BASE64ISH = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_HEXISH = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_NON_ALPHANUM = re.compile(r"[^0-9a-z]+")

# The correlation marker must survive a CORRECT gateway. A random hex nonce is
# high-entropy and a conforming secret detector redacts it. Evocative nouns are worse
# still: `amber`, `olive`, `sage`, `rowan` and `willow` are given names, so a gateway
# with a conforming PERSON detector redacts them and fails an honest implementation.
# These are mundane concrete nouns that no NER model tags as an entity. Five drawn
# from 96 give 96**5 ~= 2**33 combinations, and correlation needs only a majority of
# them back, so redacting one or two still ties the capture to this run. (The prior
# list claimed ~2**32 for four words drawn from 54, which was really 2**23.)
_NONCE_WORDS = (
    "anchor anvil apron awning barrel basin beacon bellows blanket bobbin bolt bracket "
    "bridle bucket buckle cabinet caliper candle canvas cauldron chisel cistern clamp "
    "cleat compass conduit crate crowbar cupboard dowel drawer funnel gasket girder "
    "granite gravel grommet gutter hamper handle hinge hopper jigsaw kettle lantern "
    "latch ladder ledger lever lintel mallet mantle mortar nozzle pallet pantry parcel "
    "pedal pewter piston plank pliers plunger pulley rafter ratchet ridge rivet rudder "
    "rung saddle sandal satchel scaffold shelf shutter sickle skillet socket spatula "
    "spindle spigot stencil stirrup tackle tarpaulin thimble tongs trestle trowel "
    "turbine valve vise winch wedge whisk"
).split()
_NONCE_WORD_COUNT = 5
# A conforming gateway may legitimately redact a minority of the marker words.
_NONCE_MIN_MATCHES = 3


def _make_nonce() -> str:
    return "-".join(secrets.choice(_NONCE_WORDS) for _ in range(_NONCE_WORD_COUNT))


def _build_prompt(nonce: str) -> str:
    return _PROMPT_TEMPLATE.format(nonce=nonce)


def _normalize(text: str) -> str:
    """Lowercase and drop every non-alphanumeric character.

    Matching on this defeats separator-level obfuscation (unicode escapes, inserted
    punctuation, whitespace) and fragments split across adjacent string literals,
    without needing a decoder for each individual trick.
    """
    return _NON_ALPHANUM.sub("", text.lower())


def _decode_candidates(value: str) -> list[str]:
    """Every plausible decoding of a string, so encoded PII is still inspected.

    Scans for encoded RUNS rather than requiring the whole string to be one: a single
    prefix character defeated an anchored whole-string base64 match.
    """
    found: list[str] = []
    for match in _BASE64ISH.findall(value):
        run = match.replace("-", "+").replace("_", "/").rstrip("=")
        # Try every 4-byte alignment. A run captured with a stray leading character
        # decodes to noise at offset 0 while the real payload starts one byte in,
        # which is all it took to hide an encoded value from an anchored match.
        for offset in range(4):
            aligned = run[offset:]
            if len(aligned) < 16:
                break
            # Both truncating to a 4-character boundary and padding up to one:
            # truncating alone drops the final group, which cost the tail of a value
            # and with it the literal match.
            for candidate in (
                aligned[: len(aligned) // 4 * 4],
                aligned + "=" * (-len(aligned) % 4),
            ):
                try:
                    decoded = base64.b64decode(candidate, validate=False).decode("utf-8", "ignore")
                except (binascii.Error, ValueError):
                    continue
                if decoded.strip():
                    found.append(decoded)
    for match in _HEXISH.findall(value):
        try:
            decoded = bytes.fromhex(match[: len(match) // 2 * 2]).decode("utf-8", "ignore")
        except ValueError:
            continue
        if decoded.strip():
            found.append(decoded)
    if "%" in value:
        unquoted = unquote_plus(value)
        if unquoted != value:
            found.append(unquoted)
    return found


class _Inspection:
    """Every string a parsed body carries, plus whether the walk saw all of it."""

    def __init__(self) -> None:
        self.strings: list[str] = []
        self.values: list[str] = []
        self.truncated = False
        self.nodes = 0

    def budget(self) -> bool:
        self.nodes += 1
        if self.nodes > _MAX_INSPECT_NODES:
            self.truncated = True
            return False
        return True


def _collect(value: Any, found: _Inspection, depth: int = 0, rounds: int = 0, is_key: bool = False) -> None:
    """Walk a parsed body exhaustively, decoding encoded runs as it goes.

    The leak check runs over this, not over raw wire bytes: any semantics-preserving
    transport encoding (JSON unicode escapes, gzip, base64, hex, char-code arrays,
    fragmented string literals) leaves the wire bytes unrecognizable while the
    upstream still parses the PII.
    """
    if not found.budget():
        return
    if depth > _MAX_INSPECT_DEPTH:
        found.truncated = True
        return
    if isinstance(value, str):
        found.strings.append(value)
        if not is_key:
            found.values.append(value)
        if rounds < _MAX_DECODE_ROUNDS:
            for decoded in _decode_candidates(value):
                _collect(decoded, found, depth, rounds + 1)
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        # Numbers carry PII too: a digit string survives as an integer, and a
        # character-code array reconstructs upstream while holding no strings at all.
        text = repr(value)
        found.strings.append(text)
        if not is_key:
            found.values.append(text)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _collect(str(key), found, depth + 1, rounds, is_key=True)
            _collect(item, found, depth + 1, rounds)
        return
    if isinstance(value, (list, tuple)):
        codes = [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
        if codes and len(codes) == len(value) and all(0 < code < 0x110000 for code in codes):
            decoded = "".join(chr(code) for code in codes)
            found.strings.append(decoded)
            found.values.append(decoded)
        for item in value:
            _collect(item, found, depth + 1, rounds)
        return
    found.truncated = True


def _leaked_entities(records: list[dict[str, Any]]) -> list[str]:
    """Entity types whose fixture value reached the upstream in any recoverable form."""
    leaked: set[str] = set()
    for record in records:
        strings: list[str] = record["strings"]
        values: list[str] = record["values"]
        haystacks = (
            "".join(strings),
            "".join(values),
            _normalize("".join(strings)),
            _normalize("".join(values)),
        )
        for entity, value in PROTECTED_VALUES.items():
            if entity in leaked:
                continue
            if any(value in text for text in strings):
                leaked.add(entity)
                continue
            normalized = _normalize(value)
            if any(value in hay or normalized in hay for hay in haystacks):
                leaked.add(entity)
    return sorted(leaked)


def _marker_matches(record: dict[str, Any], words: list[str]) -> int:
    """How many marker words survived into this captured request."""
    hay = _normalize("".join(record["strings"]))
    return sum(1 for word in dict.fromkeys(words) if _normalize(word) in hay)


def _decode_content_encoding(body: bytes, content_encoding: str) -> tuple[bytes, Optional[str]]:
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return body, None
    try:
        if encoding == "gzip":
            return gzip.decompress(body), None
        if encoding in ("deflate", "zlib"):
            return zlib.decompress(body), None
    except (OSError, EOFError, zlib.error):
        return body, f"undecodable_{encoding}"
    return body, f"unsupported_encoding_{encoding}"


def _timestamp() -> str:
    source_epoch = os.getenv("SOURCE_DATE_EPOCH")
    current = (
        datetime.fromtimestamp(int(source_epoch), tz=timezone.utc)
        if source_epoch is not None
        else datetime.now(timezone.utc)
    )
    return current.isoformat().replace("+00:00", "Z")


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    def at(fraction: float) -> float:
        return ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)]

    return {
        "mean": statistics.fmean(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
    }


class _CaptureState:
    """Records what the upstream actually received, decoded, not as raw wire bytes."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.records: list[dict[str, Any]] = []

    def append(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.records)


def _handler_for(state: _CaptureState):
    class CaptureHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            self._capture()
            if self.path.rstrip("/") == "/v1/models":
                body = json.dumps(
                    {"object": "list", "data": [{"id": "conformance-model", "object": "model"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def _read_body(self) -> tuple[bytes, Optional[str]]:
            """Read the body under either framing.

            BaseHTTPRequestHandler does not decode Transfer-Encoding: chunked, and
            httpx/aiohttp/requests all switch to chunked automatically for streaming
            bodies. Reading content-length alone yields an empty capture that would
            otherwise be indistinguishable from a clean one.
            """
            transfer_encoding = (self.headers.get("transfer-encoding") or "").lower()
            if "chunked" in transfer_encoding:
                chunks: list[bytes] = []
                total = 0
                while True:
                    size_line = self.rfile.readline(65536).strip()
                    if not size_line:
                        return b"".join(chunks), "truncated_chunked_body"
                    try:
                        size = int(size_line.split(b";")[0], 16)
                    except ValueError:
                        return b"".join(chunks), "malformed_chunk_header"
                    if size == 0:
                        while True:
                            trailer = self.rfile.readline(65536)
                            if trailer in (b"\r\n", b"\n", b""):
                                break
                        return b"".join(chunks), None
                    total += size
                    if total > _MAX_CAPTURE_BYTES:
                        return b"".join(chunks), "body_too_large"
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
            raw_length = self.headers.get("content-length")
            if raw_length is None:
                return b"", "missing_framing"
            try:
                length = int(raw_length)
            except ValueError:
                return b"", "invalid_content_length"
            if length > _MAX_CAPTURE_BYTES:
                return b"", "body_too_large"
            return self.rfile.read(length), None

        def _capture(self) -> tuple[dict[str, Any], Any]:
            body, framing_error = self._read_body()
            body, encoding_error = _decode_content_encoding(
                body, self.headers.get("content-encoding", "")
            )
            record: dict[str, Any] = {
                "path": self.path,
                "method": self.command,
                "parsed": False,
                "strings": [],
                "values": [],
                "byte_length": len(body),
                "error": framing_error or encoding_error,
            }
            payload = None
            found = _Inspection()
            # The request line is inspected too: a query string is as much an egress
            # channel as a body, and it is where a GET would carry the payload.
            _collect(unquote_plus(self.path), found)
            # So are request headers. A gateway can redact the visible message field
            # and carry the raw values in metadata headers instead; the upstream
            # receives them either way, so an unwalked header is an unwatched channel.
            for header_name, header_value in self.headers.items():
                _collect(str(header_name), found)
                _collect(unquote_plus(str(header_value)), found)
            try:
                payload = json.loads(body)
                _collect(payload, found)
                record["strings"] = found.strings
                record["values"] = found.values
                if found.truncated:
                    # Fail closed. A body too deep or too large to walk has NOT been
                    # shown to be clean, and a target can choose how deep to nest.
                    record["error"] = record["error"] or "inspection_truncated"
                # A declared content-encoding the capture could not apply means the
                # bytes walked are not the bytes a real upstream would decode, even
                # when they happen to parse as JSON on their own.
                record["parsed"] = not record["error"]
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                record["strings"] = found.strings
                record["values"] = found.values
                if body.strip():
                    record["error"] = record["error"] or f"unparsable_body:{type(exc).__name__}"
                else:
                    # A bodyless request (a GET, say) is fully inspected via its
                    # request line; absence of JSON is not an inspection failure.
                    record["parsed"] = not found.truncated
            # Always recorded, parsed or not: an inspection failure must be able to
            # fail the boundary check rather than silently count as a clean capture.
            state.append(record)
            return record, payload

        def do_POST(self) -> None:  # noqa: N802
            # Every path is captured and inspected, not just chat/completions. The
            # boundary under test is the configured upstream ORIGIN: a target that
            # posts raw PII to a sibling endpoint (embeddings, moderations, a
            # telemetry route) has leaked it just as surely.
            record, payload = self._capture()
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_error(404)
                return
            if not record["parsed"]:
                self.send_error(400)
                return
            try:
                messages = payload.get("messages", [])
                content = messages[-1].get("content", "") if messages else ""
                if not isinstance(content, str):
                    content = json.dumps(content, separators=(",", ":"))
            except (AttributeError, IndexError, TypeError):
                self.send_error(400)
                return

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            for character in content:
                event = {"choices": [{"delta": {"content": character}}]}
                line = f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        # Any other verb is still egress. Capture it, then refuse it.
        def do_PUT(self) -> None:  # noqa: N802
            self._capture()
            self.send_error(405)

        do_PATCH = do_PUT
        do_DELETE = do_PUT
        do_OPTIONS = do_PUT

    return CaptureHandler


async def _exercise_target(
    target_base_url: str,
    api_key: str,
    model: str,
    iterations: int,
    timeout_seconds: float,
    extra_headers: dict[str, str],
    prompt: str,
) -> dict[str, Any]:
    url = urljoin(target_base_url.rstrip("/") + "/", "chat/completions")
    headers = {"authorization": f"Bearer {api_key}", **extra_headers}
    durations_ms: list[float] = []
    # Scored per iteration. Keeping only the last one lets a target that mangles
    # every earlier iteration report clean on the strength of its final response.
    texts: list[str] = []
    event_counts: list[int] = []
    invalid_counts: list[int] = []
    marker_counts: list[int] = []
    content_types: list[str] = []
    status_codes: list[int] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for iteration in range(iterations):
            started = time.perf_counter()
            reconstructed: list[str] = []
            event_count = 0
            invalid_events = 0
            done_markers = 0
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers={**headers, "x-session-id": f"conformance-{iteration}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                    },
                ) as response:
                    status_codes.append(response.status_code)
                    content_types.append(response.headers.get("content-type", ""))
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done_markers += 1
                            continue
                        try:
                            event = json.loads(data)
                            value = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if isinstance(value, str):
                                reconstructed.append(value)
                            event_count += 1
                        except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
                            invalid_events += 1
                durations_ms.append((time.perf_counter() - started) * 1_000)
                texts.append("".join(reconstructed))
                event_counts.append(event_count)
                invalid_counts.append(invalid_events)
                marker_counts.append(done_markers)
            except (httpx.HTTPError, UnicodeError) as exc:
                errors.append(type(exc).__name__)

    completed = len(texts)
    return {
        "durations_ms": durations_ms,
        "iterations_requested": iterations,
        "iterations_completed": completed,
        "iterations_matching": sum(1 for text in texts if text == prompt),
        "text_matches": completed == iterations and all(text == prompt for text in texts),
        "events": min(event_counts) if event_counts else 0,
        "events_max": max(event_counts) if event_counts else 0,
        "invalid_events": sum(invalid_counts),
        "done_markers_valid": completed == iterations and all(m == 1 for m in marker_counts),
        "content_type_valid": completed == iterations
        and all("text/event-stream" in ct.lower() for ct in content_types),
        "content_type": content_types[-1] if content_types else "",
        "status_codes": sorted(set(status_codes)),
        "errors": errors,
    }


def run_http_conformance(
    target_base_url: str,
    *,
    api_key: str = "conformance-key",
    model: str = "conformance-model",
    implementation_name: str = "external-openai-compatible-endpoint",
    implementation_version: str = "unspecified",
    iterations: int = 3,
    timeout_seconds: float = 30.0,
    capture_host: str = "127.0.0.1",
    capture_port: int = 8765,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Evaluate an OpenAI-compatible endpoint against a controlled capture upstream.

    Configure the target gateway's upstream base URL to the capture server before running.
    Use ``capture://self`` as the target to record an explicit raw-pass-through baseline.
    """
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    # Per-run nonce. Without it nothing ties a captured request to THIS run, so a
    # target can exfiltrate raw PII to its real upstream and satisfy the boundary
    # check with one unrelated request to the capture server.
    nonce = _make_nonce()
    prompt = _build_prompt(nonce)

    state = _CaptureState()
    server = ThreadingHTTPServer((capture_host, capture_port), _handler_for(state))
    actual_host, actual_port = server.server_address[:2]
    capture_base_url = f"http://{actual_host}:{actual_port}/v1"
    effective_target = capture_base_url if target_base_url == "capture://self" else target_base_url
    thread = threading.Thread(target=server.serve_forever, name="conformance-capture", daemon=True)
    thread.start()
    try:
        exercise = asyncio.run(
            _exercise_target(
                effective_target,
                api_key,
                model,
                iterations,
                timeout_seconds,
                extra_headers or {},
                prompt,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    captured = state.snapshot()
    marker_words = nonce.split("-")
    leaked_types = _leaked_entities(captured)
    # A conforming gateway may redact part of the marker, so a majority ties the
    # capture to this run. Requiring the literal nonce failed honest implementations.
    correlated = [
        record for record in captured if _marker_matches(record, marker_words) >= _NONCE_MIN_MATCHES
    ]
    uninspectable = [record for record in captured if not record["parsed"]]
    paths = sorted({str(record["path"]).split("?")[0] for record in captured})
    latency = _percentiles(exercise["durations_ms"])
    checks = {
        "configured_upstream_boundary": {
            "passed": bool(correlated) and not leaked_types and not uninspectable,
            "captured_requests": len(captured),
            "correlated_requests": len(correlated),
            "uninspectable_requests": len(uninspectable),
            "uninspectable_reasons": sorted(
                {str(record["error"]) for record in uninspectable if record["error"]}
            ),
            "leaked_entity_types": leaked_types,
            "upstream_paths_observed": paths,
            "marker_words_required": _NONCE_MIN_MATCHES,
            "marker_words_total": len(marker_words),
            "inspection_scope": (
                "every request to the capture origin on any path or method: request line "
                "plus body, after transfer-encoding and content-encoding decoding, walked "
                "recursively over all JSON types, with base64/hex/percent-encoded runs and "
                "character-code arrays decoded, matched literally and with separators removed"
            ),
            "payload_content_included": False,
        },
        "fragmentation_safety": {
            "passed": exercise["text_matches"] and exercise["events"] > 1,
            "one_character_events_requested": True,
            "events_observed": exercise["events"],
            "events_observed_max": exercise["events_max"],
            "coalescing_not_distinguished": True,
        },
        "sse_validity": {
            "passed": (
                exercise["invalid_events"] == 0
                and exercise["done_markers_valid"]
                and exercise["content_type_valid"]
                and not exercise["errors"]
            ),
            "invalid_events": exercise["invalid_events"],
            "done_markers_valid": exercise["done_markers_valid"],
            "status_codes": exercise["status_codes"],
            "errors": exercise["errors"],
        },
        "response_fidelity": {
            "passed": exercise["text_matches"],
            "expected_value_reconstructed": exercise["text_matches"],
            "iterations_matching": exercise["iterations_matching"],
            "iterations_completed": exercise["iterations_completed"],
            "payload_content_included": False,
        },
        "client_observed_latency": {
            "passed": len(exercise["durations_ms"]) == iterations,
            "threshold_enforced": False,
            "unit": "milliseconds",
            "iterations": iterations,
            **latency,
        },
    }
    from llm_shield_proxy.conformance import build_attestation

    attestation = build_attestation()
    report: dict[str, Any] = {
        "schema": "llm-shield.streaming-privacy-http-profile/v1.0.0",
        "generated_at": _timestamp(),
        "profile": {
            "name": "OpenAI-compatible HTTP gateway profile",
            "scope": "client-to-gateway request, controlled configured-upstream capture, and SSE response",
        },
        "implementation": {
            "name": implementation_name,
            "version": implementation_version,
            "labels_are_operator_supplied": True,
        },
        "target": {
            "base_url": target_base_url,
            "model": model,
            "extra_header_names": sorted(extra_headers or {}),
            "raw_pass_through_baseline": target_base_url == "capture://self",
        },
        "harness_revision": os.getenv("GITHUB_SHA") or os.getenv("LLM_SHIELD_SOURCE_REVISION") or "unknown",
        "environment": {
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "capture": {
            "bind_host": capture_host,
            "port": actual_port,
            "target_must_be_preconfigured_for": capture_base_url,
        },
        "checks": checks,
        "passed": all(check["passed"] for check in checks.values()),
        "limitations": [
            "The target must be configured to use the harness capture server as its upstream.",
            "This HTTP profile does not evaluate gateway process RSS, audit evidence, or public-model behavior.",
            "The synthetic fixture does not establish population-level detector accuracy.",
            "Client-observed latency includes local HTTP and capture-server work and has no universal threshold.",
            "Implementation name and version are operator-supplied labels, not measured identity.",
            "Fragmentation safety verifies more than one event, which does not distinguish "
            "per-token streaming from a fully buffered response emitted as a few chunks.",
            "Any attestation block is self-reported run metadata, not third-party verification.",
            "Leak detection covers the configured capture origin only. Egress to any other "
            "destination is outside what this profile can observe.",
            "Inspection recovers encoded and fragmented values, but a value split across "
            "non-adjacent fields of a body is not reassembled.",
        ],
    }
    if attestation is not None:
        report["attestation"] = attestation
    return report
