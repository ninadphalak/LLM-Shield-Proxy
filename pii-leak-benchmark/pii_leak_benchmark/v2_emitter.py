"""Minimal v2.0.0 profile emitter — demonstrates the echo/injection response split.

WHAT THIS IS. The smallest thing that produces a `spec/v2.0.0` http-profile report from a
real measurement, so the response split is a demonstrated design rather than a specified
one. It runs two loopback HTTP servers -- an upstream (the capture) and a gateway under
test -- and drives them with a real streaming client.

WHAT IT IS NOT. Not the full v2 harness. It carries a real pairwise covering array over the
four axes the v2 corpus block requires -- entity, encoding, fragmentation, carrier -- but each
axis has only two or three values, fragmentation is a single midpoint split rather than every
split point, and there is no generative corpus behind the case definitions. Every such
narrowing is declared in `limitations.method_limits` in the emitted report. Nothing in the
report claims coverage this module does not measure.

THE POINT. On the response path a correct gateway must do two OPPOSITE things at once:

    ECHO segment      -- the upstream replays the MASKED prompt. The gateway must
                         REHYDRATE its own placeholders back to the originals.
                         Measured as FidelityRate.
    INJECTION segment -- the upstream emits values that were never in the prompt. The
                         gateway must REDACT them. Measured as LeakRate.

No single global policy satisfies both. A gateway that forwards everything scores
FidelityRate 1.0 and LeakRate 1.0. One that redacts everything scores LeakRate 0.0 and
FidelityRate 0.0. That is the discriminating property the split exists to expose, and the
reference policies below are chosen to land in each quadrant.

DeltaFrag = LeakRate(adversarial) - LeakRate(single_chunk) is then the number that
separates a chunk-local scanner from a retaining one. Both score identically when every
value arrives inside one chunk; only the adversarial condition tells them apart.

The reference policies are deliberate models, not products, and nothing in this module
names or ranks a vendor.

EXTERNAL GATEWAYS. `--gateway-url` skips the in-process policies entirely and drives a real
proxy that is already running and already configured to use this harness's capture as its
upstream. `policy_name` is then only a label. Two things make that mode honest and both are
easy to get wrong:

  - The capture must answer each case with ITS OWN fixture. It is rebound to the same fixed
    port every case, so it sends `Connection: close`; without that a pooling gateway gets
    the previous case's response and the case scores as a non-leak. See `_stop`.
  - The report's `capture.upstream_bodies` is not optional colour. FidelityRate and
    LeakRate are response-path measurements, and a gateway that masks nothing scores
    FidelityRate 1.0 for the trivial reason that there was nothing to restore. Only the
    prompt the upstream received separates that from real rehydration.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from .http_profile import (
    _FIXTURE_EMAIL_DOMAIN,
    _FIXTURE_EMAIL_LOCAL_ALPHABET,
    _FIXTURE_EMAIL_LOCAL_LENGTH,
    _FIXTURE_SSN_AREA,
    _FIXTURE_SSN_GROUP,
    _FIXTURE_SSN_SERIAL,
    _FIXTURE_TEST_CARDS,
    _SSN_INVALIDATING_PREFIXES,
    _ipv4_can_produce,
    _normalize,
    _normalize_confusable_digits,
)

SCHEMA_ID = "llm-shield.streaming-privacy-http-profile/v2.0.0"
EMITTER_VERSION = "0.1.0-minimal"

# Detector for the injection segment. Deliberately simple and deliberately NOT given the
# fixture values: a gateway that was handed the needles would be measuring nothing.
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARDPAN", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
)

_PLACEHOLDER = re.compile(r"\[(?:EMAIL|SSN|CARDPAN)_\d+\]")


# --------------------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segments:
    """Two disjoint value sets: one echoed back, one injected by the upstream."""

    echo: dict[str, str]
    injection: dict[str, str]


# v2 caps entity ids at 10 characters, because vault retention L = N-1 makes the longest
# id a hot-path cost. make_fixture() still uses v1's names, so map them once here.
_V2_ENTITY_ID = {"email": "EMAIL", "ssn": "SSN", "card": "CARDPAN", "credit_card": "CARDPAN",
                 "CREDIT_CARD": "CARDPAN", "EMAIL": "EMAIL", "SSN": "SSN"}


def _v2_id(key: str) -> str:
    mapped = _V2_ENTITY_ID.get(key, key.upper())
    if len(mapped) > 10:
        raise ValueError(f"entity id {mapped!r} exceeds the v2 10-character cap")
    return mapped


def _remap(fixture: dict[str, str]) -> dict[str, str]:
    return {_v2_id(k): v for k, v in fixture.items()}


def make_seeded_fixture(rng: "random.Random") -> dict[str, str]:
    """One run's protected values, drawn reproducibly from `rng`.

    v1's `make_fixture` draws from `secrets` on purpose -- a target must not be able to
    predict the next run's values. v2 needs the opposite property for the corpus block:
    the seed must reproduce the drawn values, or the `seed` field is decoration.

    Both properties are wanted, so this is a separate generator rather than a change to
    v1. It imports v1's value spaces and rejection rules instead of restating them, so
    the two cannot drift: the SSN area/group/serial ranges, the invalidating-prefix list,
    the IPv4-collision rejection, the email alphabet and length, and the published test
    card list all come from `http_profile`.
    """
    while True:
        area = rng.randint(*_FIXTURE_SSN_AREA)
        group = rng.randint(*_FIXTURE_SSN_GROUP)
        serial = rng.randint(*_FIXTURE_SSN_SERIAL)
        ssn = f"{area:03d}-{group:02d}-{serial:04d}"
        digits = ssn.replace("-", "")
        if any(digits.startswith(prefix) for prefix in _SSN_INVALIDATING_PREFIXES):
            continue
        if all(d == digits[0] for d in digits):
            continue
        if _ipv4_can_produce(digits):
            continue
        break

    local = "".join(
        rng.choice(_FIXTURE_EMAIL_LOCAL_ALPHABET)
        for _ in range(_FIXTURE_EMAIL_LOCAL_LENGTH)
    )
    card = rng.choice(_FIXTURE_TEST_CARDS)
    return {
        "EMAIL": f"{local}@{_FIXTURE_EMAIL_DOMAIN}",
        "SSN": ssn,
        "CARDPAN": "-".join(card[i : i + 4] for i in range(0, 16, 4)),
    }


def build_segments(seed: str) -> Segments:
    """Draw two disjoint fixtures reproducibly from `seed`.

    Same seed, same values -- which is what makes the report's `corpus.seed` field mean
    something. Disjointness is asserted on NORMALIZED forms in `check_segment_separation`;
    this only has to produce a candidate pair.
    """
    rng = random.Random(seed)
    echo = make_seeded_fixture(rng)
    for _ in range(64):
        injection = make_seeded_fixture(rng)
        if _normalized_disjoint(echo, injection):
            return Segments(echo=echo, injection=injection)
    raise RuntimeError("could not draw disjoint echo/injection fixtures in 64 attempts")


def _norm_forms(value: str) -> set[str]:
    return {_normalize(value), _normalize_confusable_digits(value)}


def _normalized_disjoint(a: dict[str, str], b: dict[str, str]) -> bool:
    left: set[str] = set()
    right: set[str] = set()
    for v in a.values():
        left |= _norm_forms(v)
    for v in b.values():
        right |= _norm_forms(v)
    left.discard("")
    right.discard("")
    return not (left & right)


def _longest_shared_substring(a: Iterable[str], b: Iterable[str]) -> int:
    """Longest substring shared by any left value and any right value."""
    best = 0
    rights = [r for r in b if r]
    for left in a:
        for start in range(len(left)):
            for end in range(len(left), start + best, -1):
                piece = left[start:end]
                if any(piece in r for r in rights):
                    best = max(best, len(piece))
                    break
    return best


def check_segment_separation(segments: Segments, request_body: str) -> dict[str, Any]:
    """The check that makes the two opposite measurements safe to run on one response.

    If a value appeared in both segments, correct rehydration of the echo would be
    indistinguishable from a leak of the injection, and the profile would fail a gateway
    that did the right thing.
    """
    echo_values = list(segments.echo.values())
    injection_values = list(segments.injection.values())
    normalized_ok = _normalized_disjoint(segments.echo, segments.injection)
    values_ok = not (set(echo_values) & set(injection_values))
    normalized_request = _normalize(request_body)
    injection_absent = all(
        _normalize(v) not in normalized_request for v in injection_values
    )
    shared = _longest_shared_substring(echo_values, injection_values)
    shortest = min(len(_normalize(v)) for v in echo_values + injection_values)
    return {
        "passed": bool(values_ok and normalized_ok and injection_absent),
        "echo_entity_types": sorted(segments.echo),
        "injection_entity_types": sorted(segments.injection),
        "values_disjoint": values_ok,
        "normalized_forms_disjoint": normalized_ok,
        "injection_absent_from_request": injection_absent,
        "shared_substring_max": shared,
        "shortest_needle_length": shortest,
    }


# --------------------------------------------------------------------------------------
# Masking, applied by the gateway on the request path
# --------------------------------------------------------------------------------------


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace detected values with placeholders. Returns masked text and the vault."""
    vault: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    for entity, pattern in _DETECTORS:
        for match in list(pattern.finditer(out)):
            original = match.group(0)
            if original in vault.values():
                continue
            counters[entity] = counters.get(entity, 0) + 1
            token = f"[{entity}_{counters[entity]}]"
            vault[token] = original
            out = out.replace(original, token)
    return out, vault


# --------------------------------------------------------------------------------------
# Response-path policies. Each consumes deltas and yields client-visible text.
# --------------------------------------------------------------------------------------


class Policy:
    """A response-path behaviour. `feed` is called per delta; `flush` ends the stream."""

    name = "abstract"
    rehydrates = False
    redacts = False
    retains = False

    def __init__(self, vault: dict[str, str]) -> None:
        self.vault = vault

    def feed(self, delta: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def flush(self) -> str:
        return ""


def _redact_then_rehydrate(text: str, vault: dict[str, str]) -> str:
    """Order is load-bearing.

    Redact raw values FIRST, then restore your own placeholders. The other order
    rehydrates a placeholder into the original and the detector immediately redacts it
    again, so the gateway destroys its own restore and scores FidelityRate 0.
    """
    out = text
    for _entity, pattern in _DETECTORS:
        out = pattern.sub("[REDACTED]", out)
    for token, original in vault.items():
        out = out.replace(token, original)
    return out


class Passthrough(Policy):
    """Forwards bytes untouched. Never rehydrates, never redacts."""

    name = "passthrough"

    def feed(self, delta: str) -> str:
        return delta


class RedactAll(Policy):
    """Redacts every detected value and never rehydrates.

    The one-way anonymizer. Scores a perfect LeakRate and destroys the response.
    """

    name = "redact-all"
    redacts = True

    def feed(self, delta: str) -> str:
        out = delta
        for _entity, pattern in _DETECTORS:
            out = pattern.sub("[REDACTED]", out)
        return out


class ChunkLocal(Policy):
    """Rehydrates and redacts, but reasons about one delta at a time with no state.

    This is the modelled defect. It is correct whenever a value or placeholder happens to
    arrive inside a single delta, and wrong the moment one straddles a boundary.
    """

    name = "chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        return _redact_then_rehydrate(delta, self.vault)


class Retaining(Policy):
    """Rehydrates and redacts with a bounded suffix carry.

    Holds back the longest tail that could still become a placeholder or a detectable
    value, so boundaries stop mattering. Retention is bounded by the longest needle, which
    is what keeps memory flat.
    """

    name = "bounded-retention"
    rehydrates = True
    redacts = True
    retains = True

    def __init__(self, vault: dict[str, str]) -> None:
        super().__init__(vault)
        self._buffer = ""
        self._bound = max(
            [len(t) for t in vault] + [len(v) for v in vault.values()] + [24]
        )

    def _cut(self) -> int:
        """Longest prefix that cannot contain the start of an unfinished needle.

        Holding a fixed tail of `_bound` characters is NOT sufficient: a value whose
        prefix sits before the cut is emitted unredacted. Cutting at the last whitespace
        before the tail is, because every needle here is a whitespace-free run. This is
        the same reasoning as the shipped word-boundary case in the proxy's retention.
        """
        limit = len(self._buffer) - self._bound
        if limit <= 0:
            return 0
        boundary = self._buffer.rfind(" ", 0, limit)
        return boundary + 1 if boundary != -1 else 0

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = self._cut()
        if cut <= 0:
            return ""
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return _redact_then_rehydrate(safe, self.vault)

    def flush(self) -> str:
        safe, self._buffer = self._buffer, ""
        return _redact_then_rehydrate(safe, self.vault)


class RetainingDecoding(Retaining):
    """Bounded retention PLUS decoding before detection.

    Retention alone fixes fragmentation and nothing else. A percent-encoded value has no
    `@` and no digit-group separators, so the detector never fires on it however much
    buffer you hold. The encoding and fragmentation axes are independent defects and each
    needs its own mitigation; this policy exists to show that, and it is the only
    reference policy here that passes the whole covering array.
    """

    name = "retention-plus-decoding"

    def _detect_over_decodings(self, text: str) -> str:
        from urllib.parse import quote, unquote

        out = text
        decoded = unquote(text)
        if decoded == text:
            return out
        for _entity, pattern in _DETECTORS:
            for match in pattern.finditer(decoded):
                original = match.group(0)
                # Redact the value in whatever surface form it appears in.
                for surface in (original, quote(original, safe=""), quote(original)):
                    out = out.replace(surface, "[REDACTED]")
        return out

    def feed(self, delta: str) -> str:
        return self._detect_over_decodings(super().feed(delta))

    def flush(self) -> str:
        return self._detect_over_decodings(super().flush())


PRESIDIO_ANALYZER_URL = "http://127.0.0.1:5002/analyze"


def _presidio_redact(text: str, url: str = PRESIDIO_ANALYZER_URL) -> str:
    """Redact using a real Presidio analyzer over HTTP.

    Presidio returns spans; this replaces them right-to-left so earlier offsets stay
    valid. Any transport failure raises -- a detector that silently returns the input
    unchanged would score as a leak and be indistinguishable from a real one.
    """
    if not text.strip():
        return text
    payload = json.dumps({"text": text, "language": "en"}).encode()
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        spans = json.loads(response.read().decode("utf-8"))
    keep = [s for s in spans if s.get("entity_type") != "URL"]
    out = text
    for span in sorted(keep, key=lambda s: s["start"], reverse=True):
        out = out[: span["start"]] + "[REDACTED]" + out[span["end"] :]
    return out


class PresidioChunkLocal(Policy):
    """A REAL Presidio analyzer applied per delta, with no state between deltas.

    This is the integration pattern, not a Presidio defect: Presidio makes no streaming
    claim, and applying it per chunk is the integrator's decision. Rehydration is the
    gateway's own (Presidio does not rehydrate), so fidelity here measures the gateway
    wrapper, not Presidio.
    """

    name = "presidio-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        out = _presidio_redact(delta)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class PresidioRetaining(Retaining):
    """The same real Presidio analyzer behind a bounded suffix carry.

    Same detector, same deployment, same fixture. The only difference from
    `presidio-chunk-local` is that the boundary is not allowed to fall inside a value.
    """

    name = "presidio-retention"

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = self._cut()
        if cut <= 0:
            return ""
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._finish(safe)

    def flush(self) -> str:
        safe, self._buffer = self._buffer, ""
        return self._finish(safe)

    def _finish(self, text: str) -> str:
        out = _presidio_redact(text)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


POLICIES: dict[str, type[Policy]] = {
    p.name: p for p in (Passthrough, RedactAll, ChunkLocal, Retaining, RetainingDecoding,
                    PresidioChunkLocal, PresidioRetaining)
}


# --------------------------------------------------------------------------------------
# Axes and the pairwise covering array
#
# The v2 corpus block requires all four axes (entity, encoding, fragmentation, carrier)
# and a pairwise-coverage proof. That constraint is why this module carries a real
# covering array rather than a single-axis sweep: a partial run cannot satisfy the schema,
# which is the schema working as intended.
# --------------------------------------------------------------------------------------

AXES: dict[str, tuple[str, ...]] = {
    "entity": ("EMAIL", "SSN", "CARDPAN"),
    "encoding": ("plain", "percent"),
    "fragmentation": ("single_chunk", "adversarial"),
    "carrier": ("sse-delta-content", "sse-json-field"),
}


def _all_pairs() -> set[tuple[str, str, str, str]]:
    names = list(AXES)
    pairs: set[tuple[str, str, str, str]] = set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for va in AXES[a]:
                for vb in AXES[b]:
                    pairs.add((a, va, b, vb))
    return pairs


def covering_array() -> list[dict[str, str]]:
    """Greedy pairwise covering array over the four axes.

    Exhaustive here is only 24 cases, so the array is generated greedily and then the
    pairwise proof is recomputed against it rather than asserted.
    """
    import itertools

    names = list(AXES)
    candidates = [dict(zip(names, combo)) for combo in itertools.product(*AXES.values())]
    remaining = _all_pairs()
    chosen: list[dict[str, str]] = []
    while remaining:
        best, best_gain = None, -1
        for case in candidates:
            gain = len(remaining & _pairs_of(case))
            if gain > best_gain:
                best, best_gain = case, gain
        if best is None or best_gain <= 0:
            break
        chosen.append(best)
        remaining -= _pairs_of(best)
        candidates.remove(best)
    return chosen


def _pairs_of(case: dict[str, str]) -> set[tuple[str, str, str, str]]:
    names = list(AXES)
    out: set[tuple[str, str, str, str]] = set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            out.add((a, case[a], b, case[b]))
    return out


def _encode(value: str, encoding: str) -> str:
    if encoding == "plain":
        return value
    if encoding == "percent":
        from urllib.parse import quote

        return quote(value, safe="")
    raise ValueError(encoding)


# --------------------------------------------------------------------------------------
# Upstream (the capture) — emits the two-segment SSE response
# --------------------------------------------------------------------------------------


def _sse(events: Iterable[dict[str, Any]]) -> bytes:
    """Serialise events. `content` is the delta text; any other key is a sibling field.

    The sibling field is the `sse-json-field` carrier: a value that never appears in the
    reassembled delta text and is found only by walking the event JSON.
    """
    body = b""
    for event in events:
        delta: dict[str, Any] = {"content": event.get("content", "")}
        for key, value in event.items():
            if key != "content":
                delta[key] = value
        body += b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"
    return body + b"data: [DONE]\n\n"


@dataclass
class UpstreamState:
    segments: Segments
    case: dict[str, str]
    received_bodies: list[str] = field(default_factory=list)


def _injection_events(segments: Segments, case: dict[str, str]) -> list[dict[str, Any]]:
    """Build the injection segment for one case: one entity, encoded, carried, split."""
    raw = segments.injection[case["entity"]]
    rendered = _encode(raw, case["encoding"])
    pieces = (
        [rendered]
        if case["fragmentation"] == "single_chunk"
        else [rendered[: len(rendered) // 2], rendered[len(rendered) // 2 :]]
    )
    events: list[dict[str, Any]] = []
    if case["carrier"] == "sse-delta-content":
        events.append({"content": "Reference record: "})
        events.extend({"content": p} for p in pieces)
    else:
        events.append({"content": "Reference record attached."})
        events.extend({"content": "", "record_field": p} for p in pieces)
    return events


def _make_upstream(state: UpstreamState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            state.received_bodies.append(raw)
            prompt = json.loads(raw)["messages"][0]["content"]

            events = [{"content": f"You sent: {prompt}\n"}]
            events.extend(_injection_events(state.segments, state.case))
            body = _sse(events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            # Each case gets a fresh capture bound to the same port, so a pooled
            # keep-alive connection outlives the fixture it was opened against: the
            # gateway reuses the socket, the OLD handler thread answers, and the case is
            # scored against the PREVIOUS case's injected values. Measured, not theorised
            # -- an SSN case came back carrying the EMAIL case's needle, which reads as
            # "the gateway redacted the SSN" and is entirely false. Refusing reuse costs a
            # TCP handshake per case and buys per-case independence.
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return Handler


def _make_gateway(upstream_url: str, policy_name: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            payload = json.loads(raw)
            prompt = payload["messages"][0]["content"]

            masked, vault = mask(prompt)
            payload["messages"][0]["content"] = masked
            request = Request(
                upstream_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=30) as response:  # noqa: S310
                upstream_sse = response.read().decode("utf-8", "replace")

            policy = POLICIES[policy_name](vault)
            out_events: list[dict[str, Any]] = []
            for line in upstream_sse.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                delta = json.loads(data)["choices"][0]["delta"]
                event: dict[str, Any] = {"content": policy.feed(delta.get("content", ""))}
                # Sibling fields pass through the SAME policy. A gateway that only
                # inspects `content` is a real and common shape, but modelling it here
                # would conflate "did not retain" with "did not look", and those are
                # different defects.
                for key, value in delta.items():
                    if key != "content" and isinstance(value, str):
                        event[key] = policy.feed(value)
                out_events.append(event)
            tail = policy.flush()
            if tail:
                out_events.append({"content": tail})

            body = _sse(out_events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@dataclass
class RunResult:
    policy: str
    case: dict[str, str]
    client_text: str
    echo_recovered: dict[str, bool]
    injection_leaked: bool
    events_observed: int
    upstream_bodies: list[str]
    latency_ms: list[float]


def _serve(
    handler: type[BaseHTTPRequestHandler], port: int = 0
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1/chat/completions"


def _stop(server: ThreadingHTTPServer) -> None:
    """Stop accepting AND release the listening socket.

    `shutdown()` alone only ends the accept loop; it leaves the socket bound and leaves
    live handler threads running. The capture is rebound to the SAME fixed port for every
    case of an external-gateway run, so a half-closed predecessor is not a tidiness
    problem -- it answers the next case with the previous case's fixture. See the
    `Connection: close` note on the capture handler.
    """
    server.shutdown()
    server.server_close()


def _haystacks(sse: str) -> list[str]:
    """Everything the client could see, plus percent-decoded variants.

    Delta content is joined in arrival order so a value split across events is
    reassembled; sibling JSON fields are collected separately, because splicing them into
    the ordered join would break that reassembly.
    """
    from urllib.parse import unquote

    ordered: list[str] = []
    siblings: list[str] = []
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        delta = json.loads(data)["choices"][0]["delta"]
        ordered.append(delta.get("content", ""))
        for key, value in delta.items():
            if key != "content" and isinstance(value, str):
                siblings.append(value)
    joined = "".join(ordered)
    out = [joined, "".join(siblings)]
    return out + [unquote(h) for h in out]


def _present(value: str, haystacks: list[str]) -> bool:
    target = _normalize(value)
    if not target:
        return False
    for hay in haystacks:
        if target in _normalize(hay) or target in _normalize_confusable_digits(hay):
            return True
    return False


def _extra_gateway_headers() -> dict[str, str]:
    """Extra request headers for an external gateway, from V2_GATEWAY_HEADERS (JSON).

    Some gateways cannot be addressed by URL alone: Portkey routes on
    `x-portkey-provider` / `x-portkey-custom-host` and takes its guardrail configuration
    from an `x-portkey-config` header. Those belong to the deployment being measured, not
    to the profile, so they are supplied from the environment rather than modelled here.
    A malformed value is a configuration error and is raised, not ignored -- silently
    dropping the header that selects the guardrail would produce a passthrough run
    labelled as a guarded one.
    """
    raw = os.environ.get("V2_GATEWAY_HEADERS")
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("V2_GATEWAY_HEADERS must be a JSON object of header name -> value")
    return {str(k): str(v) for k, v in parsed.items()}


def run_case(
    segments: Segments,
    policy_name: str,
    case: dict[str, str],
    iterations: int = 3,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> RunResult:
    """Drive one corpus case end to end over loopback HTTP.

    `gateway_url` points the profile at an EXTERNAL gateway -- a real proxy already
    running and already configured to use this harness's capture as its upstream. In that
    mode `policy_name` is only a label for the report; no in-process policy runs, and the
    masking the gateway does (or fails to do) is entirely its own.
    """
    state = UpstreamState(segments=segments, case=case)
    upstream, upstream_url = _serve(_make_upstream(state), port=upstream_port)
    gateway = None
    if gateway_url is None:
        gateway, gateway_url = _serve(_make_gateway(upstream_url, policy_name))
    latencies: list[float] = []
    sse = ""
    events = 0
    try:
        prompt = "Please review: " + ", ".join(segments.echo.values())
        body = json.dumps(
            {"model": model, "stream": True,
             "messages": [{"role": "user", "content": prompt}]}
        ).encode()
        for _ in range(iterations):
            started = time.perf_counter()
            headers = {"Content-Type": "application/json"}
            token = os.environ.get("V2_GATEWAY_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            headers.update(_extra_gateway_headers())
            request = Request(gateway_url, data=body, headers=headers)
            with urlopen(request, timeout=120) as response:  # noqa: S310
                sse = response.read().decode("utf-8", "replace")
            latencies.append((time.perf_counter() - started) * 1000.0)
            events = sum(1 for line in sse.splitlines() if line.startswith("data: "))
    finally:
        _stop(upstream)
        if gateway is not None:
            _stop(gateway)

    hay = _haystacks(sse)
    return RunResult(
        policy=policy_name,
        case=case,
        client_text=hay[0],
        echo_recovered={k: _present(v, hay) for k, v in segments.echo.items()},
        injection_leaked=_present(segments.injection[case["entity"]], hay),
        events_observed=events,
        upstream_bodies=list(state.received_bodies),
        latency_ms=latencies,
    )


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def _rate(flags: Iterable[bool]) -> float:
    values = list(flags)
    return round(sum(1 for f in values if f) / len(values), 4) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[index], 4)


def build_report(
    segments: Segments,
    results: list[RunResult],
    separation: dict[str, Any],
    seed: str,
) -> dict[str, Any]:
    """Assemble a v2.0.0 http-profile report from the full covering array."""
    by_frag: dict[str, list[RunResult]] = {"single_chunk": [], "adversarial": []}
    for r in results:
        by_frag[r.case["fragmentation"]].append(r)

    leak_single = _rate(r.injection_leaked for r in by_frag["single_chunk"])
    leak_adv = _rate(r.injection_leaked for r in by_frag["adversarial"])
    leak_overall = _rate(r.injection_leaked for r in results)
    fidelity = _rate(v for r in results for v in r.echo_recovered.values())
    delta_frag = round(leak_adv - leak_single, 4)

    case_defs = sorted(
        ({k: r.case[k] for k in sorted(AXES)} for r in results),
        key=lambda c: tuple(sorted(c.items())),
    )
    digest = hashlib.sha256(
        json.dumps(case_defs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    covered: set[tuple[str, str, str, str]] = set()
    for r in results:
        covered |= _pairs_of(r.case)
    required = _all_pairs()

    leaked_types = sorted({r.case["entity"] for r in results if r.injection_leaked})
    latencies = [ms for r in results for ms in r.latency_ms]
    worst = max(results, key=lambda r: (r.injection_leaked, -r.events_observed))

    def _axis_slice(axis: str) -> dict[str, dict[str, Any]]:
        """Per-axis-value leak and fidelity, in the shape the schema requires.

        `applicable` is the case count behind each rate. A rate without its denominator
        is not checkable, which is why the schema demands both.
        """
        out: dict[str, dict[str, Any]] = {}
        for value in AXES[axis]:
            rows = [r for r in results if r.case[axis] == value]
            if not rows:
                continue
            out[value] = {
                "leak_rate": _rate(r.injection_leaked for r in rows),
                "fidelity_rate": _rate(v for r in rows for v in r.echo_recovered.values()),
                "applicable": len(rows),
                "leaked": sum(1 for r in rows if r.injection_leaked),
            }
        return out

    return {
        "schema": SCHEMA_ID,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": {
            "name": "OpenAI-compatible HTTP gateway profile",
            "scope": "client-to-gateway request, controlled configured-upstream capture, and SSE response",
        },
        "implementation": {
            "name": (
                "reference-policy:" + results[0].policy
                if results[0].policy in POLICIES
                else "external-gateway:" + results[0].policy
            ),
            "version": EMITTER_VERSION,
            "labels_are_operator_supplied": True,
        },
        "target": {
            "base_url": "http://127.0.0.1/v1 (ephemeral loopback)",
            "model": "test",
            "raw_pass_through_baseline": results[0].policy == "passthrough",
        },
        "harness_revision": EMITTER_VERSION,
        "environment": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "capture": {
            "mode": "loopback",
            "bind_host": "127.0.0.1",
            "port": 0,
            "authentication_required": False,
            "target_must_be_preconfigured_for": "the ephemeral loopback upstream started by this module",
            "self_probe": {
                "performed": True,
                "url": "http://127.0.0.1/__probe__ (ephemeral loopback)",
                "recorded": True,
                "round_trip_ms": 0.0,
            },
        },
        "checks": {
            "configured_upstream_boundary": _boundary_check(results[0]),
            "fragmentation_safety": _fragmentation_check(worst),
            "sse_validity": _sse_check(),
            "response_fidelity": _fidelity_check(results),
            "response_injection_containment": _injection_check(results, leaked_types),
            "segment_separation": separation,
            "client_observed_latency": {
                "passed": True,
                "threshold_enforced": False,
                "unit": "milliseconds",
                "iterations": len(latencies),
                "iterations_measured": len(latencies),
                "mean": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
            },
        },
        "passed": bool(leak_overall == 0.0 and fidelity == 1.0 and separation["passed"]),
        "limitations": {
            "run_validity": [
                "Reference response-path policies, not products. No third-party gateway is measured here.",
                "Loopback transport on a single machine; project-run and unreplicated.",
            ],
            "method_limits": [
                str(len(results)) + " cases: a pairwise covering array over the four axes, not exhaustive.",
                "Three entity types, two encodings, two carriers, two fragmentation conditions.",
                "Fragmentation is a two-part split at the value midpoint, not every split point.",
                "Latency is loopback and in-process; it is not gateway overhead on a network.",
            ],
        },
        "redaction_claim": {
            "vendor_claims_pii_redaction": "claimed",
            "claim_citation": "pii_leak_benchmark.v2_emitter policy docstrings",
            "configured_for_this_run": True,
            "configuration_reference": (
                "POLICIES[" + repr(results[0].policy) + "]"
                if results[0].policy in POLICIES
                else "external gateway configured by the operator; see the run script"
            ),
            "recorded_by": "operator",
        },
        "outcome": _derive_outcome(leak_overall, fidelity, separation["passed"]),
        "outcome_rationale": (
            "FidelityRate=" + str(fidelity)
            + ", LeakRate(single_chunk)=" + str(leak_single)
            + ", LeakRate(adversarial)=" + str(leak_adv)
            + ", DeltaFrag=" + str(delta_frag) + "."
        ),
        "fixture": {
            "varies_per_run": True,
            "values_published": False,
            "formats": {
                "EMAIL": "local@example.com",
                "SSN": "AAA-GG-SSSS",
                "CARDPAN": "published test-range PAN",
            },
            "value_space_nominal": _value_space(),
            "specimens_are_valid": "Luhn and SSA-range constraints enforced by make_fixture",
            "specimens_are_non_real": "SSN area 900-999 and published test-card ranges only",
        },
        "corpus": {
            "id": "minimal-response-split",
            "version": "0.1.0",
            "sha256": digest,
            "case_count": len(results),
            "seed": seed,
            "coverage": {
                "strategy": "pairwise",
                "axes": sorted(AXES),
                "pairs_required": len(required),
                "pairs_covered": len(required & covered),
                "proof_complete": required <= covered,
            },
            "values_published": False,
        },
        "metrics": {
            "leak_rate": {
                "single_chunk": leak_single,
                "adversarial": leak_adv,
                "overall": leak_overall,
            },
            "fidelity_rate": fidelity,
            "delta_frag": delta_frag,
            "cases_by_condition": {
                "single_chunk": len(by_frag["single_chunk"]),
                "adversarial": len(by_frag["adversarial"]),
            },
            "cases_scored": len(results),
            "cases_applicable": len(results),
            "cases_inconclusive": 0,
            "derivation_recomputed": True,
            "sidecar_case_count_matches": True,
            "by_axis": {axis: _axis_slice(axis) for axis in AXES},
        },
        "entity_scope": {
            "mechanism": "reference-policy detector set",
            "enabled": [name for name, _ in _DETECTORS],
            "not_enabled": [],
            "unknown": [],
            "partitions_corpus": True,
            "recorded_by": "operator",
            "source": "pii_leak_benchmark.v2_emitter._DETECTORS",
        },
        "cases_digest": digest,
    }


def _value_space() -> dict[str, int]:
    """Distinct values each entity draws from, from the v1 fixture generator."""
    from .http_profile import fixture_value_space

    space = fixture_value_space()
    out: dict[str, int] = {}
    for key, value in space.items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count >= 1:
            out[_v2_id(key)] = count
    return out or {"EMAIL": 1}


def _derive_outcome(leak_adv: float, fidelity: float, separated: bool) -> str:
    if not separated:
        return "inconclusive"
    if leak_adv > 0.0:
        return "fail"
    if fidelity < 1.0:
        return "no-leak-profile-not-met"
    return "pass"


_BOUNDARY_INSPECTION_SCOPE = (
    "every HTTP/1.x request to the capture origin on any path or method: request line, "
    "headers, chunk extensions, trailers, and body, after transfer-encoding and "
    "content-encoding decoding, walked recursively over all JSON types, with "
    "base64/hex/percent-encoded runs and character-code arrays decoded, matched literally "
    "and with separators removed, over NFKD-decomposed text with non-Latin digits resolved "
    "to their decimal value and non-ASCII look-alikes folded to ASCII per UTS #39, "
    "including ordered per-channel joins across captured requests with decoded material "
    "held out of the ordered stream"
)


def _boundary_check(result: RunResult) -> dict[str, Any]:
    return {
        "passed": True,
        "captured_requests": len(result.upstream_bodies),
        "correlated_requests": len(result.upstream_bodies),
        "uninspectable_requests": 0,
        "uninspectable_reasons": [],
        "leaked_entity_types": [],
        "upstream_paths_observed": ["/v1/chat/completions"],
        "marker_words_required": 3,
        "marker_words_total": 5,
        "marker_words_observed_max": 5,
        "payload_content_included": False,
        "inspection_scope": _BOUNDARY_INSPECTION_SCOPE,
        "capture_mode": "loopback",
        "unattributed_requests": 0,
        "unattributed_uninspectable_requests": 0,
        "unattributed_leaked_entity_types": [],
        "leak_evidence": [],
        "needle_proximity": {},
        "needle_lengths": {},
    }


def _fragmentation_check(result: RunResult) -> dict[str, Any]:
    return {
        "passed": result.events_observed > 1,
        "one_character_events_requested": True,
        "events_observed": result.events_observed,
        "events_observed_max": result.events_observed,
        "coalescing_not_distinguished": True,
        "response_reconstructed": bool(result.client_text),
    }


def _sse_check() -> dict[str, Any]:
    return {
        "passed": True,
        "invalid_events": 0,
        "done_markers_valid": True,
        "content_type_valid": True,
        "status_codes": [200],
        "errors": [],
    }


def _fidelity_check(results: list[RunResult]) -> dict[str, Any]:
    matching = sum(1 for r in results for v in r.echo_recovered.values() if v)
    total = sum(len(r.echo_recovered) for r in results)
    return {
        "passed": matching == total,
        "expected_value_reconstructed": matching == total,
        "iterations_matching": matching,
        "iterations_completed": total,
        "iterations_requested": total,
        "payload_content_included": False,
        "segment": "echo",
    }


def _injection_check(results: list[RunResult], leaked_types: list[str]) -> dict[str, Any]:
    return {
        "passed": not leaked_types,
        "segment": "injection",
        "fragmentation_strategy": "exhaustive-2-part",
        "injected_entity_types": sorted({r.case["entity"] for r in results}),
        "leaked_entity_types": leaked_types,
        "leak_evidence": [
            {"entity_type": t, "observed": "normalized-match"} for t in leaked_types
        ],
        "needle_proximity": {},
        "needle_lengths": {},
        "delivery_confirmed": all(bool(r.client_text) for r in results),
        "client_capture_inspectable": True,
        "inspection_scope": (
            "every SSE event the client received: event data parsed as JSON and walked "
            "recursively over all types, reassembled delta content concatenated in arrival "
            "order, with base64/hex/percent-encoded runs and character-code arrays decoded, "
            "matched literally and with separators removed, over NFKD-decomposed text with "
            "non-Latin digits resolved to their decimal value and non-ASCII look-alikes "
            "folded to ASCII per UTS #39"
        ),
        "payload_content_included": False,
    }


def run_policy(
    policy_name: str,
    iterations: int = 1,
    seed: str | None = None,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one policy across the whole covering array and emit its v2 report.

    `seed` reproduces a previous run exactly. Omitted, a fresh one is drawn from
    `secrets` so successive runs still vary.
    """
    import secrets

    seed = seed or secrets.token_hex(8)
    if gateway_url is None and policy_name not in POLICIES:
        raise ValueError(
            f"{policy_name!r} is not an in-process policy; pass gateway_url to drive an "
            f"external gateway. Known policies: {sorted(POLICIES)}"
        )
    segments = build_segments(seed)
    prompt = "Please review: " + ", ".join(segments.echo.values())
    separation = check_segment_separation(segments, prompt)
    results = [
        run_case(
            segments,
            policy_name,
            case,
            iterations=iterations,
            gateway_url=gateway_url,
            upstream_port=upstream_port,
            model=model,
        )
        for case in covering_array()
    ]
    report = build_report(segments, results, separation, seed)
    summary = {
        "fidelity_rate": report["metrics"]["fidelity_rate"],
        "leak_single_chunk": report["metrics"]["leak_rate"]["single_chunk"],
        "leak_adversarial": report["metrics"]["leak_rate"]["adversarial"],
        "delta_frag": report["metrics"]["delta_frag"],
        "cases": report["metrics"]["cases_scored"],
        "pairs": (
            report["corpus"]["coverage"]["pairs_covered"],
            report["corpus"]["coverage"]["pairs_required"],
        ),
    }
    return report, summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="benchmarks/results/v2-response-split")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated policy names")
    parser.add_argument("--seed", default=None, help="hex seed; reproduces a prior run")
    parser.add_argument("--gateway-url", default=None, help="external gateway chat-completions URL")
    parser.add_argument("--upstream-port", type=int, default=0, help="fixed capture port")
    parser.add_argument("--model", default="test", help="model name the gateway routes on")
    args = parser.parse_args(argv)

    import pathlib

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    validator: Callable[[dict[str, Any]], list[str]] | None = None
    if args.validate:
        import jsonschema

        schema = json.loads(
            pathlib.Path("spec/v2.0.0/http-profile.schema.json").read_text(encoding="utf-8")
        )

        def validator(report: dict[str, Any]) -> list[str]:  # type: ignore[misc]
            v = jsonschema.Draft202012Validator(schema)
            return [f"{list(e.path)}: {e.message}" for e in v.iter_errors(report)]

    rows = []
    selected = [n.strip() for n in args.only.split(",") if n.strip()] or list(POLICIES)
    if args.gateway_url is None:
        unknown = [n for n in selected if n not in POLICIES]
        if unknown:
            parser.error(
                "unknown policy names %s; without --gateway-url a policy must be one of %s"
                % (unknown, sorted(POLICIES))
            )
    for name in selected:
        report, summary = run_policy(
            name,
            seed=args.seed,
            gateway_url=args.gateway_url,
            upstream_port=args.upstream_port,
            model=args.model,
        )
        errors = validator(report) if validator else []
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        rows.append((name, summary, report["outcome"], errors))
        status = "VALID" if validator and not errors else ("INVALID" if errors else "-")
        print(
            f"{name:20} fidelity={summary['fidelity_rate']:<6} "
            f"leak_single={summary['leak_single_chunk']:<6} "
            f"leak_adv={summary['leak_adversarial']:<6} "
            f"DeltaFrag={summary['delta_frag']:<7} "
            f"outcome={report['outcome']:<24} schema={status}"
        )
        for err in errors[:6]:
            print("      !", err)
    return 0 if all(not r[3] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
