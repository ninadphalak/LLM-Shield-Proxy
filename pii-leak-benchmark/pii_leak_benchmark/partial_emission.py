"""Partial emission of a later-masked value: a separately-versioned response-path check.

THE GAP. `v2_emitter._leak_tier` reports a leak only when the COMPLETE injected value can
be recovered from what the client received. A gateway that forwards `user@exa` in one
delta and then, having recognised the value across the chunk boundary, sends
`[REDACTED]` for the rest, delivers `user@exa[REDACTED]`. No tier matches and the case is
scored as contained, although the client received most of a live address. That is the
fixed-carryover failure: the carryover catches the value, but only after the first
fragment is already on the wire. The published scorer asserts the NECESSARY condition
(the whole value must not arrive) and not the SUFFICIENT one (no fragment of a
later-masked value may be emitted).

WHY A SEPARATE INSTRUMENT. Every published v2 row carries `instrument.inspector_sha256`,
a digest of the scorer functions in `v2_emitter._INSTRUMENTED`, and the test suite marks
a row stale the moment the current code's digest differs. Teaching `_leak_tier` or
`run_case` about partial emission would change what every published PASS means without
any of those rows having been produced under the new assertion. So this module scores
the new tier in its own pass, writes its own artefact under its own schema id
(`PARTIAL_EMISSION_SCHEMA_ID`), and fingerprints its own source
(`instrument.partial_emission_sha256`). The v2 report, its leak rates and its digest are
untouched. A reader can take or discount this tier on its own, the same reason
`cross-field-join` is ranked separately.

TWO ORACLES, chosen by who owns the policy:

* In process (`gateway_url is None`) the harness owns the `Policy`, so the exact
  assertion is available: run the same policy over the whole upstream stream as ONE
  delta and compare byte for byte with what it emitted chunk by chunk. A correct
  streaming redactor is chunking-invariant. Inequality catches partial emission,
  double-masking and redaction corruption alike; it is scored as partial emission only
  when the chunked output also exposes a longer run of the injected value than the
  whole-stream output does, and the complete value was not recovered (that is a stronger
  tier, already scored by the v2 report).
* Against an external gateway (`--gateway-url`) the whole-stream filter cannot be run
  on someone else's proxy. The response-side port of `http_profile._needle_proximity`
  is used instead: the complete value did not arrive (so the gateway changed it), and a
  contiguous run of it of at least `MIN_SPECIFIC_RUN` characters did, counting only
  text the client did not itself send. See `MIN_SPECIFIC_RUN` for how the threshold was
  measured.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote

import httpx

from .http_profile import _normalize
from .v2_emitter import (
    _DETECTORS,
    _LEAK_TIER_RANK,
    AXES,
    DEFAULT_PARTITION_CAP,
    POLICIES,
    UpstreamState,
    _behaviour_source,
    _client_timeout,
    _extra_gateway_headers,
    _injection_events,
    _leak_tier,
    _make_gateway,
    _make_upstream,
    _ordered_channels,
    _parse_sse,
    _self_probe,
    _serve,
    _stop,
    build_request,
    build_segments,
    covering_array,
    injection_partitions,
    inspector_digest,
)

PARTIAL_EMISSION_SCHEMA_ID = "llm-shield.partial-emission/v1.0.0"
# Bump when anything that decides a published field of this artefact changes. It is
# carried beside `partial_emission_sha256`, which moves on its own when the source does.
PARTIAL_EMISSION_VERSION = "1.0.0"

TIER = "partial-emission"

# THE EXTERNAL-GATEWAY THRESHOLD, and how it was chosen.
#
# What is compared is `specific_run`: the longest contiguous run of the injected value,
# in `_normalize`d form (case-folded, punctuation dropped: an email is 18 characters, a
# card 16, a phone number 10, an SSN 9), that appears in a response channel outside any
# complete well-formed value, and that does NOT occur in text the client itself sent or
# the capture put around the value. Both exclusions are measured necessities, not taste:
#
# * The echo and injection emails always share `example.com`, and several published test
#   PANs share long runs of `1`. The raw longest run of the needle in a CORRECT response
#   (`http_profile._needle_proximity` over the published haystacks) was 10-13 of 18 for
#   every email case and up to 14 of 16 for cards.
# * With client-supplied text excluded but the chunk envelope still searched, a ten-digit
#   `created` timestamp and a random `id` pushed the benign maximum to 7 (SSN, card and
#   phone digits). Hence `_ENVELOPE_PATHS`.
# * A gateway that substitutes a fixture-shaped fake value emits text shaped exactly like
#   the needle (another test PAN, another `@example.com` address). Hence
#   `_outside_whole_values`: before it, substitutes produced benign runs of 13 of 16.
#
# Measured by `benchmarks/partial_emission_threshold.py --seeds 20000`: 4,480,000
# simulated correct responses (20,000 seeds x the 32-case covering array x 7 replacement
# styles: `[REDACTED]`, Presidio-style `<EMAIL_ADDRESS>` tags, `[EMAIL_1]` placeholders,
# `****`, nothing, a sentence of gateway prose, and a fixture-shaped synthetic
# substitute), each with an OpenAI-style chunk envelope on every event. The maximum
# `specific_run` was 4, reached only by emails and only by letters of the replacement
# text meeting letters of the random local part (244 of 160,000 email responses in the
# prose style, 52 in the `[REDACTED]` style); every SSN, card and phone case measured 0.
# The tail falls roughly fifteen-fold per character (prose style: 3,538 at 3, 244 at 4).
#
# 5 is therefore the smallest threshold no benign response reached. At the midpoint cut
# this detects the email (9 characters exposed), card (8) and phone (5) fragments; an SSN
# midpoint fragment is 4 normalised digits and is below it, and is caught only by the
# exhaustive oracles, where longer prefixes are cut. Cross-checked in process against the
# exact oracle (`--http-seeds 10`, every two-part split, 2,520 partitions per policy):
# `bounded-retention` and `retention-plus-decoding` had 0 invariance violations and 0
# threshold flags; for `redact-all` and `chunk-local` the threshold flagged 60 of the
# 249 and 251 partitions the exact oracle scored as partial emission, and never one it
# cleared. The rest are fragments of one to four characters, which only the in-process
# oracle can see.
MIN_SPECIFIC_RUN = 5

THRESHOLD_BASIS = (
    "smallest run no benign response reached: maximum specific_run 4 over 4,480,000 "
    "simulated correct responses (20,000 seeds x 32 cases x 7 replacement styles, "
    "including fixture-shaped substitutes and a chunk envelope); "
    "benchmarks/partial_emission_threshold.py reproduces it"
)

_CAPTURE_TEXT = ("You sent: ",)


def benign_texts(segments: Any, case: dict[str, str], model: str = "test") -> list[str]:
    """Every string the client or the capture legitimately put around the value.

    The request body the harness sent (so every echo value, the prompt template and the
    model name) and the capture's own text on either side of the injected value. A
    fragment of the injected value that also occurs in one of these cannot be told apart
    from an echo, so it is not evidence of anything.
    """
    texts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                texts.append(str(key))
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)
        elif isinstance(node, str):
            texts.append(node)

    _walk(build_request(segments, case, model=model))
    texts.extend(_CAPTURE_TEXT)
    # The carrier preamble ("Reference record: ..."), everything except the value itself.
    texts.extend(str(event.get("content", "")) for event in _injection_events(segments, case)[:1])
    return [t for t in texts if t]


def _outside_whole_values(text: str) -> list[str]:
    """`text` cut apart at every complete, well-formed value of any detected entity shape.

    A whole value that is not the injected one (the complete injected value is a
    stronger tier and is ruled out before this runs) is a SUBSTITUTE, not a fragment: a
    gateway that replaces a card with another published test PAN emits thirteen
    consecutive `1`s that the needle may share. A fragment followed by a mask is, by
    construction, not a well-formed value, so it survives the cut. Splitting rather
    than blanking keeps a run from bridging the removed span once `_normalize` drops
    the punctuation around it.
    """
    pieces = [text]
    for _entity, pattern in _DETECTORS:
        pieces = [part for piece in pieces for part in pattern.split(piece)]
    return pieces


def specific_run(needle: str, haystacks: list[str], benign: list[str]) -> int:
    """Longest run of `needle` found in `haystacks` and in none of `benign`.

    The response-side port of `http_profile._needle_proximity`, with the one addition
    that keeps it usable as a verdict: a run the client could have got from its own
    request is not counted. Both sides are compared in `_normalize`d form, the fold the
    published scorer matches in, and percent-encoded haystacks are also read decoded.
    """
    target = _normalize(needle)
    hays = [_normalize(piece) for h in haystacks for form in (h, unquote(h)) for piece in _outside_whole_values(form)]
    known = [_normalize(b) for b in benign]
    longest = 0
    length = len(target)
    for start in range(length):
        # Only runs longer than the best so far can improve it.
        for end in range(length, start + longest, -1):
            fragment = target[start:end]
            if any(fragment in k for k in known):
                continue
            if any(fragment in h for h in hays):
                longest = end - start
                break
    return longest


# The OpenAI chunk envelope: fields a gateway generates itself, never text it relays.
# `created` is a ten-digit integer and `id` a random alphanumeric string, and measured
# against this corpus they are the ONLY source of benign runs above 3 (a timestamp shares
# five consecutive digits with an SSN, card or phone value often enough to matter; see
# `MIN_SPECIFIC_RUN`). The complete-value tiers still search them -- this exclusion is
# local to the partial-emission tier, which is the weakest claim and must not be set by
# a clock.
_ENVELOPE_PATHS = frozenset(
    {
        ".id",
        ".object",
        ".created",
        ".model",
        ".system_fingerprint",
        ".service_tier",
        ".choices[].index",
        ".choices[].finish_reason",
    }
)


def response_haystacks(sse: str) -> list[str]:
    """Per-channel joins of everything the client received, minus the chunk envelope.

    The same per-path join the published `same-path-join` group uses, so a fragment split
    across events in one field is one run. Events that are not JSON, and bytes the SSE
    parser did not dispatch, are included raw.
    """
    parsed = _parse_sse(sse)
    flat: list[tuple[str, str]] = []
    unparsed: list[str] = []
    for payload in parsed.events:
        if payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            unparsed.append(payload)
            continue
        _ordered_channels(event, flat)
    channels: dict[str, list[str]] = {}
    for path, value in flat:
        if path in _ENVELOPE_PATHS or path == ".usage" or path.startswith(".usage."):
            continue
        channels.setdefault(path, []).append(value)
    return ["".join(values) for values in channels.values()] + unparsed + list(parsed.residue)


def partial_emission_tier(
    needle: str,
    sse: str,
    benign: list[str],
    threshold: int = MIN_SPECIFIC_RUN,
) -> str | None:
    """The external-gateway verdict for one response: `"partial-emission"` or None.

    None when the complete value was recovered: that is a stronger tier, scored by the
    v2 report, and this one must not claim it.
    """
    if _leak_tier(needle, sse) is not None:
        return None
    if specific_run(needle, response_haystacks(sse), benign) >= threshold:
        return TIER
    return None


@dataclass
class PartialEmissionResult:
    policy: str
    case: dict[str, str]
    # "chunking-invariance" in process, "needle-run-threshold" against a gateway URL.
    oracle: str
    partition_oracle: str
    needle_length: int
    transport_error: str | None = None
    partitions_tried: int = 0
    # Partitions scored as partial emission by `oracle`.
    partitions_partial: int = 0
    # Partitions where the complete value was recovered (the v2 report's finding).
    complete_leak_partitions: int = 0
    # Partitions the threshold detector flags, whichever oracle decides. In process this
    # is a cross-check of the threshold against the exact oracle.
    threshold_partitions: int = 0
    # In process only: partitions whose chunked output differs from the whole-stream
    # output. None against an external gateway, where it cannot be computed.
    invariance_violations: int | None = None
    # In process only: partitions the threshold detector flags and the exact oracle
    # clears, i.e. threshold false positives. None against an external gateway.
    threshold_only_partitions: int | None = None
    # Longest `specific_run` in any partition where the complete value was NOT
    # recovered. A length, never a value.
    longest_specific_run: int = 0
    partial_emission: bool = False


def score_case(
    segments: Any,
    policy_name: str,
    case: dict[str, str],
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
    oracle: str = "midpoint",
    partition_cap: int = DEFAULT_PARTITION_CAP,
) -> PartialEmissionResult:
    """Drive one case over loopback HTTP, every partition of `oracle`, and score it.

    A second pass, separate from `v2_emitter.run_case` on purpose: that function is inside
    the published digest. The request, the capture and the partitions are the same ones
    it uses, so the two passes are measuring the same corpus.
    """
    in_process = gateway_url is None
    if in_process and policy_name not in POLICIES:
        raise ValueError(f"{policy_name!r} is not an in-process policy; pass gateway_url")
    needle = segments.injection[case["entity"]]
    benign = benign_texts(segments, case, model=model)
    points, _families, _attempted, _capped = injection_partitions(segments, case, oracle=oracle, cap=partition_cap)
    row = PartialEmissionResult(
        policy=policy_name,
        case=dict(case),
        oracle="chunking-invariance" if in_process else "needle-run-threshold",
        partition_oracle=oracle,
        needle_length=len(_normalize(needle)),
        invariance_violations=0 if in_process else None,
        threshold_only_partitions=0 if in_process else None,
    )
    state = UpstreamState(segments=segments, case=case)
    upstream, upstream_url = _serve(_make_upstream(state), port=upstream_port)
    recorder: list[dict[str, Any]] = []
    gateway = None
    try:
        _self_probe(upstream_url, state)
        if in_process:
            gateway, gateway_url = _serve(_make_gateway(upstream_url, policy_name, recorder=recorder))
        assert gateway_url is not None
        body = json.dumps(build_request(segments, case, model=model)).encode()
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("V2_GATEWAY_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers.update(_extra_gateway_headers())
        headers["Connection"] = "close"
        with httpx.Client(timeout=_client_timeout(), trust_env=False) as client:
            for cuts in points:
                state.cuts = cuts
                captured_before = len(state.received_bodies)
                recorded_before = len(recorder)
                try:
                    response = client.post(gateway_url, content=body, headers=headers)
                    if response.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"{response.status_code} {response.reason_phrase}",
                            request=response.request,
                            response=response,
                        )
                    sse = response.content.decode("utf-8", "replace")
                except Exception as exc:  # noqa: BLE001
                    # Refused or timed out: inconclusive, never a clean partition.
                    row.transport_error = f"{type(exc).__name__}: {exc}"
                    break
                if len(state.received_bodies) == captured_before:
                    row.transport_error = (
                        "capture recorded no request: the response did not come from this "
                        "run's upstream, so nothing about the gateway was measured"
                    )
                    break
                row.partitions_tried += 1
                complete = _leak_tier(needle, sse) is not None
                run = specific_run(needle, response_haystacks(sse), benign)
                if not complete:
                    # A complete value is a stronger tier; its run length says nothing here.
                    row.longest_specific_run = max(row.longest_specific_run, run)
                over_threshold = (not complete) and run >= MIN_SPECIFIC_RUN
                row.complete_leak_partitions += int(complete)
                row.threshold_partitions += int(over_threshold)
                if in_process:
                    entries = recorder[recorded_before:]
                    if len(entries) != 1:
                        row.transport_error = f"reference gateway recorded {len(entries)} policy runs for one request"
                        break
                    emitted = "".join(entries[0]["emitted"])
                    reference = entries[0]["reference"]
                    violated = emitted != reference
                    row.invariance_violations = (row.invariance_violations or 0) + int(violated)
                    partial = (
                        violated
                        and not complete
                        and specific_run(needle, [emitted], benign) > specific_run(needle, [reference], benign)
                    )
                    row.threshold_only_partitions = (row.threshold_only_partitions or 0) + int(
                        over_threshold and not partial
                    )
                else:
                    partial = over_threshold
                row.partitions_partial += int(partial)
    finally:
        _stop(upstream)
        if gateway is not None:
            _stop(gateway)
    row.partial_emission = row.transport_error is None and row.partitions_partial > 0
    return row


def _rate(rows: list[PartialEmissionResult]) -> float:
    return round(sum(1 for r in rows if r.partial_emission) / len(rows), 4) if rows else 0.0


def _cases_digest(results: list[PartialEmissionResult]) -> str:
    """The same digest the v2 report publishes as `cases_digest`, so the two join."""
    case_defs = sorted(
        ({k: r.case[k] for k in sorted(AXES)} for r in results),
        key=lambda c: tuple(sorted(c.items())),
    )
    return hashlib.sha256(json.dumps(case_defs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_FINGERPRINTED = (
    benign_texts,
    _outside_whole_values,
    specific_run,
    response_haystacks,
    partial_emission_tier,
    score_case,
    _make_gateway,
)


def partial_emission_digest() -> str:
    """Fingerprint of this instrument's deciding source and its threshold."""
    parts = [_behaviour_source(function) for function in _FINGERPRINTED]
    parts.append(f"MIN_SPECIFIC_RUN={MIN_SPECIFIC_RUN}")
    parts.append(repr(sorted(_ENVELOPE_PATHS)))
    parts.append(repr([(entity, pattern.pattern) for entity, pattern in _DETECTORS]))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def instrument_block() -> dict[str, str]:
    return {
        "partial_emission_version": PARTIAL_EMISSION_VERSION,
        "partial_emission_sha256": partial_emission_digest(),
        # The published v2 scorer this pass defers to for "was the complete value
        # recovered". Unchanged by this instrument; recorded so the pairing is checkable.
        "base_inspector_sha256": inspector_digest(),
    }


def build_partial_emission_report(
    results: list[PartialEmissionResult],
    seed: str,
    gateway_url: str | None = None,
    model: str = "test",
) -> dict[str, Any]:
    """The partial-emission artefact. Carries lengths and counts, never a value."""
    in_process = gateway_url is None
    policy = results[0].policy if results else "unknown"
    scored = [r for r in results if r.transport_error is None]
    inconclusive = [r for r in results if r.transport_error is not None]
    by_frag = {
        arm: [r for r in scored if r.case["fragmentation"] == arm] for arm in ("single_chunk", "adversarial")
    }
    partial = [r for r in scored if r.partial_emission]
    oracle = "chunking-invariance" if in_process else "needle-run-threshold"
    oracles = {r.partition_oracle for r in results}
    return {
        "schema": PARTIAL_EMISSION_SCHEMA_ID,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "implementation": {
            "name": ("reference-policy:" if in_process else "external-gateway:") + policy,
            "labels_are_operator_supplied": True,
        },
        "target": {
            "base_url": gateway_url or "in-process loopback reference policy",
            "model": model,
        },
        "tier": {
            "name": TIER,
            "rank": _LEAK_TIER_RANK[TIER],
            "ranked_after": "cross-field-join",
            "counted_in_leak_rate": False,
            "meaning": (
                "a fragment of the injected value reached the client and the complete value "
                "did not; scored separately from, and never folded into, the v2 report's "
                "injection leak rate"
            ),
        },
        "oracle": {
            "name": oracle,
            "description": (
                "the policy's chunked output compared byte for byte with its output for the "
                "whole stream fed as one delta; partial emission when they differ and the "
                "chunked output exposes a longer run of the value"
                if in_process
                else "a contiguous run of the value, not supplied by the client, of at least "
                "min_specific_run normalised characters in a response channel, when the "
                "complete value was not recovered"
            ),
            "min_specific_run": MIN_SPECIFIC_RUN,
            "threshold_basis": THRESHOLD_BASIS,
            "partition_oracle": oracles.pop() if len(oracles) == 1 else "mixed",
        },
        "corpus": {
            "seed": seed,
            "case_count": len(results),
            "cases_digest": _cases_digest(results),
            "values_published": False,
        },
        "metrics": {
            "cases_applicable": len(scored),
            "cases_inconclusive": len(inconclusive),
            "cases_partial_emission": len(partial),
            "partial_emission_rate": {
                "single_chunk": _rate(by_frag["single_chunk"]),
                "adversarial": _rate(by_frag["adversarial"]),
                "overall": _rate(scored),
            },
            "cases_complete_leak": sum(1 for r in scored if r.complete_leak_partitions),
            "partitions_tried": sum(r.partitions_tried for r in scored),
            "partitions_partial": sum(r.partitions_partial for r in scored),
            "chunking_invariance_violations": (
                sum(r.invariance_violations or 0 for r in scored) if in_process else None
            ),
        },
        "partial_emission_entity_types": sorted({r.case["entity"] for r in partial}),
        "cases": [{k: v for k, v in asdict(r).items() if k != "policy"} for r in results],
        "passed": bool(results) and not inconclusive and not partial,
        "values_published": False,
        "limitations": [
            "A separate pass from the v2 report: its own requests, over the same corpus, "
            "seed and partitions. Against a nondeterministic gateway the two passes are "
            "two samples.",
            "Complete-value recovery is decided by the published v2 scorer; this artefact "
            "adds only the weaker tier and never changes a v2 leak rate or outcome.",
            "External gateways: fragments shorter than min_specific_run normalised "
            "characters, fragments of a value in a non-identity encoding, and fragments "
            "that also occur in the request (the shared email domain) are not detected.",
            "External gateways: a gateway that emits fixture-shaped synthetic substitutes "
            "can in principle reach the threshold by coincidence; this is why the tier is "
            "ranked weakest.",
        ],
        "instrument": instrument_block(),
    }


def run_partial_emission(
    policy_name: str,
    seed: str | None = None,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
    oracle: str = "midpoint",
    partition_cap: int = DEFAULT_PARTITION_CAP,
) -> dict[str, Any]:
    """Score one policy or gateway across the whole covering array. Pass the v2 run's
    seed to make the artefact describe the same fixture as that run's report."""
    import secrets

    seed = seed or secrets.token_hex(8)
    segments = build_segments(seed)
    results = [
        score_case(
            segments,
            policy_name,
            case,
            gateway_url=gateway_url,
            upstream_port=upstream_port,
            model=model,
            oracle=oracle,
            partition_cap=partition_cap,
        )
        for case in covering_array()
    ]
    return build_partial_emission_report(results, seed, gateway_url=gateway_url, model=model)
