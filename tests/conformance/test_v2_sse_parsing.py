"""The response is parsed the way a client parses it, and joined the way a client joins it.

Two false passes lived here, and both had the direction a leak instrument must never
fail in: the value reached the client and the case scored as "did not leak".

**1. The parser was a `startswith("data: ")` test.** Every other byte of the response was
discarded. `data:` without the optional space is legal SSE (WHATWG HTML 9.2.6, "if the
value starts with a U+0020 SPACE, remove it" -- the space is *optional*, not required),
a multi-line `data` payload is ONE event joined with U+000A rather than several, and a
gateway is free to answer a 200 that is not a stream at all. Measured end to end: a relay
that redacted nothing and re-emitted with `data:` scored LeakRate 0.00/0.00 where the
identical relay with `data: ` scored 1.00/1.00.

**2. The ordered joins were keyed by hand-picked key names.** `delta.content` had a
stream, "everything else" had a stream, and any key called `content` or `text` -- at any
depth -- was skipped from the second one to keep it out of the first. So a value split
across two events under `delta.raw.text` was in neither ordered stream, and the fallback
join interleaves object keys between the halves, so it did not reassemble there either.
The reference `chunk-local` policy went from DeltaFrag 0.875 to -0.125 purely by renaming
its output field, and with a detector that also percent-decodes -- so nothing leaks
unfragmented, still chunk-local -- the renamed run scored a clean `pass`. Channels are now
keyed by JSON path.

Every case below is a FALSE PASS if it regresses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    CONTENT_CHANNEL,
    RunResult,
    _fragmentation_check,
    _haystacks,
    _ordered_channels,
    _parse_sse,
    _present,
    _sse_check,
    build_segments,
    injection_partitions,
)

# `_fragmentation_check` derives `one_character_events_requested` from the pieces the
# harness would actually emit, so it needs the fixture the case was drawn from.
_SEGMENTS = build_segments("a1b2c3d4e5f60001")

NEEDLE = "nuwpcbba@example.com"


def _seen(body: str) -> bool:
    return _present(NEEDLE, _haystacks(body))


def _event(delta: dict) -> dict:
    return {"choices": [{"delta": delta}]}


# --------------------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------------------


def test_the_space_after_the_colon_is_optional() -> None:
    body = "data:" + json.dumps(_event({"content": NEEDLE})) + "\n\ndata:[DONE]\n\n"
    assert _seen(body)


def test_exactly_one_leading_space_is_stripped_not_all_of_them() -> None:
    """`data:  x` carries a leading space in its payload. Two spaces is data, not syntax."""
    assert _parse_sse("data:  hello\n\n").events == [" hello"]
    assert _parse_sse("data: hello\n\n").events == ["hello"]
    assert _parse_sse("data:hello\n\n").events == ["hello"]


def test_a_multi_line_data_payload_is_one_event_joined_with_newlines() -> None:
    payload = json.dumps(_event({"content": NEEDLE}))
    half = len(payload) // 2
    # A pretty-printed or hard-wrapped event: the spec joins the lines, so the JSON
    # parses. Reading each line as its own event loses it.
    body = f"data: {payload[:half]}\ndata: {payload[half:]}\n\ndata: [DONE]\n\n"
    assert _parse_sse(body).events[0] == payload[:half] + "\n" + payload[half:]


@pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r"])
def test_every_line_terminator_the_spec_allows(terminator: str) -> None:
    body = ("data: " + json.dumps(_event({"content": NEEDLE}))).replace("\n", terminator)
    assert _seen(body + terminator * 2)


def test_a_non_streaming_json_body_is_still_inspected() -> None:
    """A gateway may answer 200 application/json. Those bytes reached the client."""
    body = json.dumps({"choices": [{"message": {"content": "Reference: " + NEEDLE}}]})
    assert _seen(body)
    assert _parse_sse(body).looked_like_sse is False


def test_bytes_the_parser_does_not_dispatch_are_still_inspected() -> None:
    """The spec tells a CLIENT to ignore these. It does not tell an INSPECTOR to."""
    assert _seen(": comment carrying " + NEEDLE + "\n\n")
    assert _seen("event: " + NEEDLE + "\n\n")
    assert _seen("x-vendor-field: " + NEEDLE + "\n\n")
    # An event with no terminating blank line is never dispatched, and it is on the wire.
    assert _seen("data: " + NEEDLE)


def test_done_and_event_counting_survive_the_rewrite() -> None:
    body = (
        "data: " + json.dumps(_event({"content": "a"})) + "\n\n"
        "data: " + json.dumps(_event({"content": "b"})) + "\n\n"
        "data: [DONE]\n\n"
    )
    parsed = _parse_sse(body)
    assert len(parsed.events) == 3
    assert "[DONE]" in parsed.events


# --------------------------------------------------------------------------------------
# Path-keyed ordered channels
# --------------------------------------------------------------------------------------


FRAGMENTED_PLACEMENTS = {
    # The one that produced a full false pass: same policy, renamed output field.
    "nested_key_named_text": lambda a, b: (
        _event({"content": "chunk.", "raw": {"text": a}}),
        _event({"content": "chunk.", "raw": {"text": b}}),
    ),
    "nested_key_named_content": lambda a, b: (
        _event({"content": "chunk.", "record": {"content": a}}),
        _event({"content": "chunk.", "record": {"content": b}}),
    ),
    "legacy_completions_text": lambda a, b: (
        {"choices": [{"delta": {"content": "chunk."}, "text": a}]},
        {"choices": [{"delta": {"content": "chunk."}, "text": b}]},
    ),
    "content_as_a_list_of_parts": lambda a, b: (
        _event({"content": [{"type": "text", "text": a}]}),
        _event({"content": [{"type": "text", "text": b}]}),
    ),
    "tool_call_arguments": lambda a, b: (
        _event({"content": "", "tool_calls": [{"function": {"arguments": a}}]}),
        _event({"content": "", "tool_calls": [{"function": {"arguments": b}}]}),
    ),
    "plain_sibling_field": lambda a, b: (
        _event({"content": "", "record_field": a}),
        _event({"content": "", "record_field": b}),
    ),
    "second_choice": lambda a, b: (
        {"choices": [{"delta": {"content": ""}}, {"delta": {"content": a}}]},
        {"choices": [{"delta": {"content": ""}}, {"delta": {"content": b}}]},
    ),
    "top_level_field": lambda a, b: (
        {"choices": [{"delta": {"content": ""}}], "note": a},
        {"choices": [{"delta": {"content": ""}}], "note": b},
    ),
}


@pytest.mark.parametrize("placement", sorted(FRAGMENTED_PLACEMENTS))
def test_a_value_split_across_two_events_is_reassembled(placement: str) -> None:
    first, second = FRAGMENTED_PLACEMENTS[placement](NEEDLE[:9], NEEDLE[9:])
    body = (
        "data: " + json.dumps(first) + "\n\n"
        "data: " + json.dumps(second) + "\n\n"
        "data: [DONE]\n\n"
    )
    assert _seen(body), (
        f"a value split across two events at {placement!r} was not reassembled. It "
        "reached the client in full and the case scores as 'did not leak'."
    )


def test_client_text_still_means_delta_content_only() -> None:
    """`delivery_confirmed` and every published row depend on hay[0] not drifting."""
    body = (
        "data: " + json.dumps(_event({"content": "visible", "raw": {"text": "hidden"}}))
        + "\n\ndata: [DONE]\n\n"
    )
    assert _haystacks(body)[0] == "visible"
    assert CONTENT_CHANNEL == ".choices[].delta.content"


def test_a_channel_only_ever_holds_text_from_its_own_path() -> None:
    """The property that makes a per-path join safe to concatenate.

    A channel is joined without separators, so if text from another path could enter it
    the join would manufacture values nothing sent -- the false-positive direction that
    produced the round 7 IPv4 finding against a gateway that had redacted correctly.
    """
    flat: list[tuple[str, str]] = []
    _ordered_channels(
        _event({"content": "alpha", "raw": {"text": "beta"}, "items": ["gamma", "delta"]}),
        flat,
    )
    channels: dict[str, list[str]] = {}
    for path, value in flat:
        channels.setdefault(path, []).append(value)

    assert channels[CONTENT_CHANNEL] == ["alpha"]
    assert channels[".choices[].delta.raw.text"] == ["beta"]
    # Indices collapse, so a list is one channel in arrival order -- which is what makes
    # a content array carried as a list of parts reassemble.
    assert channels[".choices[].delta.items[]"] == ["gamma", "delta"]
    assert set(channels) == {
        CONTENT_CHANNEL,
        ".choices[].delta.raw.text",
        ".choices[].delta.items[]",
    }


# --------------------------------------------------------------------------------------
# The checks that used to be constants
# --------------------------------------------------------------------------------------


def _result(**kwargs) -> RunResult:
    base = dict(
        policy="p",
        case={"entity": "EMAIL", "encoding": "plain", "fragmentation": "single_chunk",
              "carrier": "sse-delta-content", "request_site": "chat-content"},
        client_text="x",
        echo_recovered={},
        echo_observable=True,
        transport_error=None,
        injection_leaked=False,
        events_observed=2,
        upstream_bodies=["{}"],
        latency_ms=[],
        status_codes=[200],
        content_types=["text/event-stream"],
        invalid_events=0,
        done_marker=True,
    )
    base.update(kwargs)
    return RunResult(**base)


def test_events_observed_max_is_a_maximum() -> None:
    """It was read off the leaking case with the FEWEST events, so it reported a minimum.

    Measured: `chunk-local` runs 16 cases at 4 events and 16 at 5, and the report said
    `events_observed_max: 4`.
    """
    results = [_result(events_observed=4), _result(events_observed=5)]
    check = _fragmentation_check(results, _SEGMENTS)
    assert check["events_observed"] == 4
    assert check["events_observed_max"] == 5


def test_sse_validity_can_fail() -> None:
    """Every field was a literal: a gateway answering application/json with no events
    was reported `content_type_valid: true, status_codes: [200], errors: []`."""
    good = _sse_check([_result()])
    assert good["passed"] is True and good["content_type_valid"] is True

    wrong_type = _sse_check([_result(content_types=["application/json"])])
    assert wrong_type["passed"] is False
    assert wrong_type["content_type_valid"] is False
    assert wrong_type["errors"]

    no_done = _sse_check([_result(done_marker=False)])
    assert no_done["passed"] is False and no_done["done_markers_valid"] is False

    unparseable = _sse_check([_result(invalid_events=2)])
    assert unparseable["passed"] is False and unparseable["invalid_events"] == 2


# --------------------------------------------------------------------------------------
# Split points
# --------------------------------------------------------------------------------------


def test_exhaustive_splits_enumerate_every_internal_offset() -> None:
    """Migrated 2026-09-09 from `injection_split_points` to `injection_partitions`.

    The single-offset enumerator was replaced by a family-aware one so a three-part
    partition could be expressed at all. The assertions are unchanged in substance: the
    midpoint is one cut at `len // 2`, the exhaustive family is every internal offset, and
    no cut leaves the whole value in one piece -- otherwise "adversarial" would silently
    contain single-chunk cases and DeltaFrag would be diluted toward zero.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    case = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
            "carrier": "sse-delta-content", "request_site": "chat-content"}
    value = segments.injection["EMAIL"]

    midpoint, _f, _a, _c = injection_partitions(segments, case)
    assert midpoint == [(len(value) // 2,)]

    exhaustive, families, attempted, capped = injection_partitions(
        segments, case, oracle="exhaustive-2-part"
    )
    assert exhaustive == [(i,) for i in range(1, len(value))]
    assert attempted == {"exhaustive-2-part": len(value) - 1}
    assert capped == {"exhaustive-2-part": False}
    assert set(families) == {"exhaustive-2-part"}
    assert all(0 < cuts[0] < len(value) for cuts in exhaustive)


def test_single_chunk_is_never_split_however_exhaustive_the_run() -> None:
    segments = build_segments("a1b2c3d4e5f60001")
    case = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "single_chunk",
            "carrier": "sse-delta-content", "request_site": "chat-content"}
    for oracle in ("midpoint", "exhaustive-2-part", "exhaustive-3-part", "union-worst-case"):
        partitions, families, attempted, _capped = injection_partitions(
            segments, case, oracle=oracle
        )
        # The empty tuple is the UNCUT attempt. It is the baseline arm, and counting it as
        # a split is exactly how `252 splits` came to be published.
        assert partitions == [()], oracle
        assert families == [] and attempted == {}, oracle


# --------------------------------------------------------------------------------------
# The capture self-probe, which was `performed: true` with no probe ever sent
# --------------------------------------------------------------------------------------


def test_the_self_probe_rejects_a_server_that_is_not_this_runs_capture() -> None:
    """A foreign server holding the capture port must abort the run, not be measured.

    `capture.self_probe` reported `performed: true, recorded: true, round_trip_ms: 0.0` in
    every report ever emitted and no probe was sent. The schema's `const true` on both
    fields assumes the opposite: that a run which cannot confirm the capture aborts.

    This is the case the later "capture recorded no request" guard cannot catch on its own,
    because that one fires after the target answers and cannot tell "the gateway never
    called upstream" from "another process owns this port" -- the Windows SO_REUSEADDR
    incident, where a stale capture produced a flawless row from a run that never happened.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from pii_leak_benchmark.v2_emitter import UpstreamState, _self_probe

    class Foreign(BaseHTTPRequestHandler):
        """Answers politely and records nothing, like a stale capture from a prior run."""

        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            return

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Foreign)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    state = UpstreamState(segments=build_segments("a1b2c3d4e5f60001"), case={})
    try:
        with pytest.raises(RuntimeError, match="self-probe failed"):
            _self_probe(f"http://{host}:{port}/v1/chat/completions", state)
    finally:
        server.shutdown()
        server.server_close()


def test_the_self_probe_leaves_no_trace_in_the_captured_bodies() -> None:
    """The probe must not be mistaken for target traffic by the boundary check."""
    import threading

    from pii_leak_benchmark.v2_emitter import (
        UpstreamState,
        _make_upstream,
        _self_probe,
        _serve,
        _stop,
    )

    case = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "single_chunk",
            "carrier": "sse-delta-content", "request_site": "chat-content"}
    state = UpstreamState(segments=build_segments("a1b2c3d4e5f60001"), case=case)
    server, url = _serve(_make_upstream(state))
    try:
        elapsed = _self_probe(url, state)
    finally:
        _stop(server)
    assert elapsed > 0.0
    assert state.received_bodies == [], (
        "the probe's own request stayed in the capture record, so it would be walked by "
        "the boundary check as if the target had sent it"
    )
    assert threading  # keep the import meaningful under -O


# --------------------------------------------------------------------------------------
# Outcome derivation now reads both paths
# --------------------------------------------------------------------------------------


def test_a_request_path_leak_forces_the_outcome_to_fail() -> None:
    """It could not, while the boundary check was a literal.

    The schema always required the pairing (`no_leak_outcome_requires_no_leak`), and it
    rejected the first real report the fixed check produced: LiteLLM, with all four values
    egressed to the upstream, was deriving `no-leak-profile-not-met` from the response path
    alone.
    """
    from pii_leak_benchmark.v2_emitter import _derive_outcome

    assert _derive_outcome(0.0, 1.0, True, False) == "pass"
    assert _derive_outcome(0.0, 1.0, True, True) == "fail"
    assert _derive_outcome(0.0, 0.0, True, False) == "no-leak-profile-not-met"
    assert _derive_outcome(0.0, 0.0, True, True) == "fail"
    # Separation failing still dominates: nothing was measurable either way.
    assert _derive_outcome(0.0, 1.0, False, True) == "inconclusive"


def test_the_derivation_assertions_are_checked_not_asserted() -> None:
    """`derivation_recomputed` and `sidecar_case_count_matches` were hardcoded `True`.

    The schema pins both `const: true` and its descriptions say what the harness is
    promising by setting them. A field that says "I checked" and is a literal is worth
    less than no field, because a reader spends trust on it.

    The FIRST repair was not enough, and this test passed against it. It took the already
    computed `leak_single`, `leak_adv` and `delta_frag` as arguments and recomputed
    `leak_adv - leak_single` -- the same expression the caller had just evaluated, from
    the same two variables -- and compared `cases_scored` (which was `len(results)`) with
    `len(case_defs)`, where `case_defs` is a comprehension over `results`. That second
    comparison is identically true for every possible input, so the field it backed was
    still a literal in effect. Both halves now rebuild from `results`.
    """
    from pii_leak_benchmark.v2_emitter import _assert_derivations, build_report

    results = [
        _result(case={**_result().case, "fragmentation": "single_chunk"}, injection_leaked=False,
                echo_recovered={"EMAIL": True}),
        _result(case={**_result().case, "fragmentation": "adversarial"}, injection_leaked=True,
                echo_recovered={"EMAIL": True}),
    ]
    separation = {"passed": True, "echo_entity_types": [], "injection_entity_types": [],
                  "values_disjoint": True, "normalized_forms_disjoint": True,
                  "injection_absent_from_request": True, "shared_substring_max": 0,
                  "shortest_needle_length": 9}
    report = build_report(build_segments("a1b2c3d4e5f60001"), results, separation, "seed")
    assert report["metrics"]["derivation_recomputed"] is True
    assert report["metrics"]["sidecar_case_count_matches"] is True

    # EVERY published metric is now rebuilt from the results, not just delta_frag.
    for path, wrong in [
        ("leak_rate.adversarial", 0.0),
        ("leak_rate.single_chunk", 1.0),
        ("leak_rate.overall", 0.9),
        ("fidelity_rate", 0.0),
        ("delta_frag", 0.5),
        ("cases_scored", 3),
        ("cases_applicable", 3),
        ("cases_inconclusive", 7),
        ("cases_echo_observable", 0),
    ]:
        tampered = json.loads(json.dumps(report["metrics"]))
        node = tampered
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = wrong
        with pytest.raises(RuntimeError, match="does not follow from"):
            _assert_derivations(results, tampered, report["cases_digest"])

    # And the digest half can fail, which it could not before.
    with pytest.raises(RuntimeError, match="cases_digest does not match"):
        _assert_derivations(results, report["metrics"], "0" * 64)
