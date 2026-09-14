"""Does a response-path privacy feature that works whole stop working when streamed?

THE QUESTION. The v2 profile has always sent ``stream: true``. Its "single-chunk" arm is
therefore *streaming with the value in one event*, which is a weaker baseline than the one
a vendor actually tests against. So the profile can say "this integration leaks" and
cannot say "this integration works until you turn streaming on" -- and the second sentence
is the one a practitioner can act on.

This probe measures three conditions against one target, holding everything else fixed:

    whole-response        stream: false, one JSON body
    single-chunk          stream: true, the protected value inside ONE SSE event
    adversarial-midpoint  stream: true, the protected value cut at len // 2

and reports, per condition, whether the caller's value was restored (fidelity) and whether
the upstream-injected value reached the client (leak).

WHY THIS IS NOT IN ``v2_emitter``. Every function that decides which bytes reach the target
or how a result is scored is listed in ``v2_emitter._INSTRUMENTED`` and feeds
``inspector_sha256``. Teaching ``_make_upstream`` to answer a non-streaming request would
move that digest, and ``tests/conformance/test_results_are_comparable.py`` then marks every
published round-8 sweep stale -- correctly, that is what the guard is for. Regenerating the
round-8 tree is a full evidence round, which the current plan explicitly does not do.

So this module adds a capture and a client of its own and **imports the scorer unchanged**.
The digest does not move, round 8 stays valid, and the two arms are still scored by exactly
the same code. Verified: ``_haystack_groups`` handles a plain JSON body through
``ParsedStream.residue``, so the non-streaming arm needs no second inspector.

WHAT IT IS NOT. Three cases, one seed, one entity per run. This is a probe and an addendum
row, not a profile run: it emits no ``outcome``, no DeltaFrag, and nothing schema-valid
against ``spec/v2.*``. Do not cite it as a conformance result.

Usage:

    # Reference control, no gateway in the path. Proves the probe itself is sound.
    python benchmarks/mode_regression_probe.py --direct --out <scratch>/direct.json

    # LiteLLM + Presidio, output_parse_pii: true
    V2_GATEWAY_TOKEN=sk-v2-profile-local \\
    python benchmarks/mode_regression_probe.py \\
        --gateway-url http://127.0.0.1:4321/v1/chat/completions \\
        --model capture --upstream-port 8799 --label litellm-presidio \\
        --out <scratch>/litellm-mode.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pii-leak-benchmark"))

from pii_leak_benchmark.artifact import write_json_artifact  # noqa: E402
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    SSE_DONE_FRAME,
    Segments,
    _haystacks,
    _injection_events,
    _leak_tier,
    _partition_pieces,
    _present,
    _sse_frames,
    build_segments,
    extract_site,
)

CONDITIONS = ("whole-response", "single-chunk", "adversarial-midpoint")

# The capture answers on this path only, matching the v2 capture's contract.
UPSTREAM_PATH_HINT = "/v1/chat/completions"


# --------------------------------------------------------------------------------------
# The capture. Streaming or not, decided by the request it receives.
# --------------------------------------------------------------------------------------


class ProbeState:
    """One case's fixtures plus whatever the upstream actually received."""

    def __init__(self, segments: Segments, case: dict[str, str], cuts: tuple[int, ...]):
        self.segments = segments
        self.case = case
        self.cuts = cuts
        self.received_bodies: list[str] = []
        self.stream_requested: list[bool] = []
        self.data_events_written = 0
        self.emitted_text = ""


def _events_for(state: ProbeState, prompt: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"content": f"You sent: {prompt}\n"}]
    events.extend(_injection_events(state.segments, state.case, state.cuts))
    return events


def _whole_response_body(events: list[dict[str, Any]]) -> bytes:
    """The non-streaming equivalent of the same events.

    THE CARRIER MAPPING, stated rather than implied. ``sse-delta-content`` becomes the
    single message's ``content``: the deltas concatenate, which is what a client does.
    ``sse-json-field`` becomes a sibling key on that same message, holding the value
    whole -- because a non-streaming response has one object, so there is no second
    sibling for the value to be split across. That IS the point of the arm: whatever the
    streaming arms make hard, this arm makes easy, and a feature that cannot restore here
    cannot restore anywhere.
    """
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    content: list[str] = []
    for event in events:
        content.append(str(event.get("content", "")))
        for key, value in event.items():
            if key == "content":
                continue
            message[key] = str(message.get(key, "")) + str(value)
    message["content"] = "".join(content)
    return json.dumps(
        {
            "id": "probe",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        }
    ).encode()


# Mask-shaped tokens: <EMAIL_ADDRESS>, <CREDIT_CARD>, [PHONE_NUMBER_2], {SSN}. Upper case,
# underscores and digits only, so ordinary prose and JSON keys do not match.
_MASK_TOKEN = re.compile(r"[<\[{]([A-Z][A-Z0-9_]{2,39})[>\]}]")


def _mask_tokens(text: str) -> list[str]:
    return sorted({m.group(1) for m in _MASK_TOKEN.finditer(text)})


def _client_text(body: str) -> str:
    """The strings a client would DISPLAY, with SSE framing removed.

    Scanning the raw body for mask tokens produced a false positive on the very first
    control run: `data: [DONE]` matches the shape `[UPPERCASE]` exactly, so every
    streamed response looked like it had acquired a redaction token. Framing is not
    content, and the fix is to parse rather than to add `DONE` to a deny list.
    """
    parts: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped.split(":", 1)[1].strip()
            if not payload or payload == "[DONE]":
                continue
        elif stripped.startswith(("{", "[")):
            payload = stripped
        else:
            continue
        try:
            document = json.loads(payload)
        except ValueError:
            continue
        for choice in document.get("choices", []) if isinstance(document, dict) else []:
            node = choice.get("delta") or choice.get("message") or {}
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if key in ("role", "finish_reason") or not isinstance(value, str):
                    continue
                parts.append(value)
    return "".join(parts)


def _upstream_emitted_text(events: list[dict[str, Any]]) -> str:
    """Everything the capture put on the wire, as one string, regardless of framing."""
    parts: list[str] = []
    for event in events:
        for value in event.values():
            parts.append(str(value))
    return "".join(parts)


def _make_probe_upstream(state: ProbeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._respond()
            except Exception as exc:  # noqa: BLE001
                # Same reason v2's capture does this: an escaped exception closes the
                # socket with no response, and the client reports a transport error for
                # what is a harness bug.
                message = json.dumps(
                    {"harness_error": f"{type(exc).__name__}: {exc}"}
                ).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(message)
                self.close_connection = True

        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            state.received_bodies.append(raw)
            parsed = json.loads(raw)
            wants_stream = bool(parsed.get("stream", False))
            state.stream_requested.append(wants_stream)

            echoed = extract_site(parsed, state.case["request_site"])
            prompt = "" if echoed is None else echoed
            events = _events_for(state, prompt)
            state.emitted_text = _upstream_emitted_text(events)

            if wants_stream:
                frames = _sse_frames(events)
                body = b"".join(frames) + SSE_DONE_FRAME
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                # Per-case independence: a pooled keep-alive connection outliving its
                # fixture is how one case gets scored against another case's needle.
                self.send_header("Connection", "close")
                self.end_headers()
                for frame in frames:
                    self.wfile.write(frame)
                    state.data_events_written += 1
                self.wfile.write(SSE_DONE_FRAME)
            else:
                body = _whole_response_body(events)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                state.data_events_written += 1

            self.wfile.flush()
            self.close_connection = True

    return Handler


def _serve(handler: type[BaseHTTPRequestHandler], port: int) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound = server.server_address[1]
    return server, f"http://127.0.0.1:{bound}{UPSTREAM_PATH_HINT}"


# --------------------------------------------------------------------------------------
# The client. Reads incrementally so first-useful-content is a measurement, not an
# estimate.
# --------------------------------------------------------------------------------------


def _first_useful(chunk_text: str) -> bool:
    """Does this SSE data frame contribute any non-whitespace CONTENT?

    NOT "is this the first event". A role-only preamble, an empty delta, a keep-alive
    comment and a whitespace-only delta are all events, and counting any of them as
    delivery is how a gateway that streams nothing useful for 900 ms reports a 4 ms
    time-to-first-byte.
    """
    if not chunk_text.startswith("data:"):
        return False
    payload = chunk_text.split(":", 1)[1].strip()
    if payload == "[DONE]" or not payload:
        return False
    try:
        document = json.loads(payload)
    except ValueError:
        # Unparseable but non-empty bytes did reach the client.
        return bool(payload.strip())
    for choice in document.get("choices", []):
        node = choice.get("delta", choice.get("message", {}))
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key in ("role", "finish_reason"):
                continue
            if isinstance(value, str) and value.strip():
                return True
    return False


def _request(
    url: str, body: dict[str, Any], token: str | None, timeout: float
) -> dict[str, Any]:
    """Send one request, return the body plus a delivery timeline in milliseconds."""
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(body).encode(), headers=headers)

    started = time.perf_counter()
    ttfb = ttfe = ttfuc = None
    pieces: list[str] = []
    status = None
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            status = response.status
            while True:
                line = response.readline()
                if not line:
                    break
                now = (time.perf_counter() - started) * 1000.0
                if ttfb is None:
                    ttfb = now
                text = line.decode("utf-8", "replace")
                pieces.append(text)
                stripped = text.strip()
                if not stripped:
                    continue
                if ttfe is None:
                    ttfe = now
                if ttfuc is None:
                    if stripped.startswith("data:"):
                        if _first_useful(stripped):
                            ttfuc = now
                    else:
                        # A non-SSE body: the first non-blank line IS the content.
                        ttfuc = now
    except HTTPError as exc:  # a status the target chose to return
        status = exc.code
        pieces.append(exc.read().decode("utf-8", "replace"))
    except (URLError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "status": status,
            "body": "".join(pieces),
            "ttfb_ms": ttfb,
            "ttfe_ms": ttfe,
            "ttfuc_ms": ttfuc,
            "total_ms": (time.perf_counter() - started) * 1000.0,
        }
    return {
        "ok": status == 200,
        "error": None,
        "status": status,
        "body": "".join(pieces),
        "ttfb_ms": ttfb,
        "ttfe_ms": ttfe,
        "ttfuc_ms": ttfuc,
        "total_ms": (time.perf_counter() - started) * 1000.0,
    }


# --------------------------------------------------------------------------------------
# One condition, end to end.
# --------------------------------------------------------------------------------------


def _build_body(
    segments: Segments, case: dict[str, str], model: str, stream: bool
) -> dict[str, Any]:
    """`v2_emitter.build_request` with the stream flag as a parameter rather than a literal."""
    text = "Please review: " + ", ".join(segments.echo.values())
    body: dict[str, Any] = {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": "Summarise the attached record."}],
    }
    site = case["request_site"]
    if site == "chat-content":
        body["messages"][0]["content"] = text
    elif site == "system-content":
        body["messages"].insert(0, {"role": "system", "content": text})
    elif site == "unrecognised-key":
        body["session_note"] = text
    elif site == "tool-description":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_record",
                    "description": text,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    else:
        raise ValueError(f"unknown request_site {site!r}")
    return body


def _median(values: list[float]) -> float | None:
    """Median, stdlib arithmetic. No numpy: the harness tree is stdlib plus httpx."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return round(clean[middle], 3)
    return round((clean[middle - 1] + clean[middle]) / 2.0, 3)


def run_condition(
    condition: str,
    segments: Segments,
    case: dict[str, str],
    *,
    target_url: str,
    model: str,
    token: str | None,
    upstream_port: int,
    timeout: float,
    repeats: int = 3,
) -> dict[str, Any]:
    """Run one condition `repeats` times.

    REPEATS EXIST FOR TWO REASONS, and neither is statistics.

    First, ordering. The conditions run in sequence, so whichever goes first pays the
    process's cold start. Measured on the no-gateway control: 39 ms for the first
    condition against 1.3 ms for the second, on identical work. A single pass would have
    published that 38 ms as the cost of not streaming, which is false. One discarded
    warm-up plus a median removes it.

    Second, stability. If the same condition scores differently across repeats, the
    result is not a measurement of the target, and saying so is more useful than
    averaging over it. `stable: false` is reported, never smoothed.
    """
    entity = case["entity"]
    rendered = segments.injection[entity]
    stream = condition != "whole-response"
    cuts: tuple[int, ...] = ()
    if condition == "adversarial-midpoint":
        cuts = (len(rendered) // 2,)
        # Assert the partition is a real fragmentation, using the emitter's own checker.
        _partition_pieces(rendered, cuts)

    attempts: list[tuple[dict[str, Any], ProbeState]] = []
    for _ in range(max(1, repeats)):
        state = ProbeState(segments, case, cuts)
        server, _url = _serve(_make_probe_upstream(state), upstream_port)
        try:
            result = _request(
                target_url, _build_body(segments, case, model, stream), token, timeout
            )
        finally:
            server.shutdown()
            server.server_close()
        attempts.append((result, state))

    # Score every attempt; the reported verdict is the one they agree on.
    echo_value_probe = segments.echo[entity]
    injected_probe = segments.injection[entity]
    verdicts = []
    for attempt_result, _attempt_state in attempts:
        attempt_body = attempt_result["body"]
        verdicts.append(
            (
                _present(echo_value_probe, _haystacks(attempt_body)) if attempt_body else None,
                (_leak_tier(injected_probe, attempt_body) is not None)
                if attempt_body
                else None,
                attempt_result["ok"],
            )
        )
    stable = len(set(verdicts)) == 1

    result, state = attempts[-1]
    body = result["body"]
    haystacks = _haystacks(body) if body else []

    echo_value = segments.echo[entity]
    injected = segments.injection[entity]

    # Fidelity is only meaningful when the request site survived to the capture. A
    # gateway may drop a key it does not recognise, and a dropped key supplies no echo to
    # restore: that is unmeasurable, not a failure. Same rule as the profile's
    # `echo_observable`.
    upstream_body = state.received_bodies[0] if state.received_bodies else None
    site_text = None
    if upstream_body:
        try:
            site_text = extract_site(json.loads(upstream_body), case["request_site"])
        except ValueError:
            site_text = None
    echo_observable = site_text is not None

    request_path_leaked = None
    if upstream_body is not None:
        request_path_leaked = _present(echo_value, _haystacks(upstream_body))

    # RESPONSE-SIDE MASKING, recorded because "not restored" has more than one mechanism
    # and they call for different repairs.
    #
    #   placeholder survived      the response path did nothing; the client sees the
    #                             gateway's own request-side token
    #   NEW mask tokens appear    the response path redacted, and it redacted text the
    #                             capture never masked -- which, when the caller's own
    #                             value is among the casualties, is the restore-then-
    #                             rescan ordering error rather than a missing feature
    #
    # Mask LABELS are not content: `<URL>` says a detector fired, not what it saw. So
    # these are safe to publish while payload_content_included stays false.
    emitted_masks = set(_mask_tokens(state.emitted_text))
    client_masks = set(_mask_tokens(_client_text(body)))
    new_masks = sorted(client_masks - emitted_masks)

    return {
        "condition": condition,
        "stream_requested_by_client": stream,
        "stream_requested_of_upstream": (
            state.stream_requested[0] if state.stream_requested else None
        ),
        "transport": {
            "ok": result["ok"],
            "status": result["status"],
            "error": result["error"],
        },
        "upstream_saw_request": upstream_body is not None,
        "echo_observable": echo_observable,
        "echo_restored": (_present(echo_value, haystacks) if echo_observable else None),
        "injection_leaked": _leak_tier(injected, body) is not None if body else None,
        "injection_leak_tier": _leak_tier(injected, body) if body else None,
        "request_path_leaked_caller_value": request_path_leaked,
        "response_side_masking": {
            "new_mask_tokens_in_client_text": new_masks,
            "suspected": bool(new_masks),
            "upstream_emitted_mask_tokens": sorted(emitted_masks),
        },
        "delivery": {
            "repeats": len(attempts),
            "ttfb_ms_median": _median([a["ttfb_ms"] for a, _ in attempts]),
            "ttfe_ms_median": _median([a["ttfe_ms"] for a, _ in attempts]),
            "ttfuc_ms_median": _median([a["ttfuc_ms"] for a, _ in attempts]),
            "total_ms_median": _median([a["total_ms"] for a, _ in attempts]),
            "ttfuc_ms_samples": [_round(a["ttfuc_ms"]) for a, _ in attempts],
        },
        "stable_across_repeats": stable,
        "upstream_data_events_written": state.data_events_written,
        "response_bytes": len(body),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# --------------------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------------------


def derive_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The one thing this probe exists to decide, derived rather than typed.

    `mode_regression` is true only when the WHOLE-RESPONSE arm is clean for a duty and
    the SINGLE-CHUNK arm is not, with both arms transported successfully. Anything else
    is `none`, including the case where the feature never worked -- which is a different
    finding and must not be dressed up as this one.
    """
    by = {row["condition"]: row for row in rows}
    whole, single = by.get("whole-response"), by.get("single-chunk")
    reasons: list[str] = []
    kinds: list[str] = []

    if not whole or not single:
        return {"mode_regression": "not-evaluated", "reasons": ["a condition is missing"]}
    if not whole["transport"]["ok"]:
        reasons.append(
            "whole-response arm did not transport "
            f"(status={whole['transport']['status']}, error={whole['transport']['error']}); "
            "a target that refuses stream:false is inconclusive here, not passing"
        )
    if not single["transport"]["ok"]:
        reasons.append("single-chunk arm did not transport")
    if reasons:
        return {"mode_regression": "inconclusive", "reasons": reasons}

    if whole["echo_restored"] is True and single["echo_restored"] is False:
        kinds.append("restoration")
    if whole["injection_leaked"] is False and single["injection_leaked"] is True:
        kinds.append("containment")

    if not kinds:
        if whole["echo_restored"] is False:
            reasons.append(
                "the whole-response arm did not restore either: this target does not "
                "restore in any mode, which is a coverage finding and not a mode regression"
            )
        if whole["injection_leaked"] is True:
            reasons.append(
                "the whole-response arm already leaked: containment is absent in both "
                "modes, so no mode regression can be attributed to streaming"
            )
        return {"mode_regression": "none", "reasons": reasons or ["both arms agree"]}

    return {
        "mode_regression": "+".join(kinds),
        "reasons": [
            "whole-response clean and single-chunk not, at equal fixtures, seed, "
            "configuration and machine"
        ],
    }


# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default=None, help="target chat-completions URL")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="no gateway: talk to the capture itself. The control that proves the probe.",
    )
    parser.add_argument("--model", default="capture")
    parser.add_argument("--token", default=None, help="defaults to $V2_GATEWAY_TOKEN")
    parser.add_argument("--upstream-port", type=int, default=8799)
    parser.add_argument("--seed", default="a1b2c3d4e5f60001")
    parser.add_argument("--entity", default="EMAIL", choices=("EMAIL", "SSN", "CARDPAN", "USPHONE"))
    parser.add_argument(
        "--carrier", default="sse-delta-content", choices=("sse-delta-content", "sse-json-field")
    )
    parser.add_argument(
        "--site",
        default="chat-content",
        choices=("chat-content", "system-content", "unrecognised-key", "tool-description"),
        help="system-content is RESERVED as held-out material; see round9/prereg/PREREG.md",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="measured passes per condition; the median is reported (default 3)",
    )
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument("--out", default=None, help="write the JSON artifact here")
    args = parser.parse_args(argv)

    import os

    token = args.token or os.environ.get("V2_GATEWAY_TOKEN")

    if args.direct and args.gateway_url:
        parser.error("--direct and --gateway-url are mutually exclusive")
    if not args.direct and not args.gateway_url:
        parser.error("pass --gateway-url, or --direct for the no-gateway control")

    segments = build_segments(args.seed)
    case = {
        "entity": args.entity,
        "encoding": "plain",
        "carrier": args.carrier,
        "request_site": args.site,
        "fragmentation": "adversarial",
    }

    target_url = args.gateway_url or f"http://127.0.0.1:{args.upstream_port}{UPSTREAM_PATH_HINT}"

    # ONE DISCARDED WARM-UP. Whichever condition runs first otherwise pays the process's
    # cold start and publishes it as that condition's delivery cost. Measured at 39 ms
    # against 1.3 ms for identical work on the no-gateway control.
    try:
        run_condition(
            CONDITIONS[0],
            segments,
            case,
            target_url=target_url,
            model=args.model,
            token=token,
            upstream_port=args.upstream_port,
            timeout=args.timeout,
            repeats=1,
        )
    except Exception as exc:  # noqa: BLE001 - a failed warm-up is not a failed run
        print(f"warm-up did not complete ({type(exc).__name__}); continuing", file=sys.stderr)

    rows = []
    for condition in CONDITIONS:
        rows.append(
            run_condition(
                condition,
                segments,
                case,
                target_url=target_url,
                model=args.model,
                token=token,
                upstream_port=args.upstream_port,
                timeout=args.timeout,
                repeats=args.repeats,
            )
        )

    verdict = derive_verdict(rows)
    report = {
        "schema": "mode-regression-probe/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": args.label,
        "target": {"url": target_url, "model": args.model, "direct": bool(args.direct)},
        "corpus": {
            "seed": args.seed,
            "case": case,
            "cases": 1,
            "measured_passes_per_condition": args.repeats,
            "discarded_warmup_passes": 1,
        },
        "conditions": list(CONDITIONS),
        "rows": rows,
        "verdict": verdict,
        "limitations": [
            "One case, one seed, one entity per run. A probe, not a profile run: it emits "
            "no outcome, no DeltaFrag, and validates against no published schema.",
            "Timings are medians over the measured passes after one discarded warm-up. "
            "A condition whose repeats disagreed on restoration or leakage is reported "
            "with stable_across_repeats=false and must not be quoted as a result.",
            "Not comparable with any v2 or FIDE report: this module has its own capture "
            "and client and is deliberately outside v2_emitter._INSTRUMENTED, so no "
            "inspector_sha256 applies to it.",
            "The scorer IS the published one: _haystacks, _present and _leak_tier are "
            "imported from v2_emitter unchanged, so the three arms are scored alike.",
            "Delivery times are loopback with a synthetic upstream on one machine. They "
            "exclude model inference and wide-area network time by construction and are "
            "not an end-to-end latency claim.",
            "response_side_masking records mask LABELS only (<URL>, <EMAIL_ADDRESS>), "
            "never the text a detector matched, so no content is published by it. A true "
            "value means the target introduced redaction tokens the capture never sent; "
            "it does not by itself prove which text was redacted.",
            "The non-streaming arm maps the sse-json-field carrier onto one sibling key "
            "of a single message, because a whole response has no second sibling for a "
            "value to be split across.",
        ],
    }

    _print_table(report)

    if args.out:
        written = write_json_artifact(pathlib.Path(args.out), report, indent=1)
        print(f"\nwrote {written}")
    return 0


def _print_table(report: dict[str, Any]) -> None:
    def cell(value: Any) -> str:
        if value is None:
            return "n/a"
        if value is True:
            return "yes"
        if value is False:
            return "no"
        return str(value)

    print(f"\n{report['label']}  seed={report['corpus']['seed']}  "
          f"case={report['corpus']['case']['entity']}/"
          f"{report['corpus']['case']['carrier']}/{report['corpus']['case']['request_site']}")
    print(f"{'condition':<22}{'http':>6}{'restored':>10}{'leaked':>8}"
          f"{'tier':>16}{'remasked':>10}{'ttfuc med':>10}")
    for row in report["rows"]:
        print(
            f"{row['condition']:<22}"
            f"{cell(row['transport']['status']):>6}"
            f"{cell(row['echo_restored']):>10}"
            f"{cell(row['injection_leaked']):>8}"
            f"{cell(row['injection_leak_tier']):>16}"
            f"{cell(row['response_side_masking']['suspected']):>10}"
            f"{cell(row['delivery']['ttfuc_ms_median']):>10}"
            f"{'' if row['stable_across_repeats'] else '  UNSTABLE'}"
        )
    print(f"\nmode_regression: {report['verdict']['mode_regression']}")
    for reason in report["verdict"]["reasons"]:
        print(f"  - {reason}")


if __name__ == "__main__":
    raise SystemExit(main())
