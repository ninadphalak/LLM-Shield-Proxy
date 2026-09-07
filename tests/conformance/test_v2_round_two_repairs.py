"""Defects found in the ROUND 1 REPAIRS, each demonstrated before being fixed.

Round 1 found ten false passes in the v2 instrument and they were all fixed in one
sitting with no second opinion. This file is the second opinion's findings, turned into
tests. Every case here is a FALSE PASS if it regresses, except the two marked otherwise.

The pattern worth naming, because it is the reason this file exists: **three of these
were introduced BY a repair**, and one of them was introduced by the repair for the
closest neighbouring defect. `_fidelity_check` was narrowed to the measurable cases to
fix a denominator, and the narrowing made a boolean pass vacuously on zero of them. A fix
that looks like the lesson is not the lesson.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    CAPTURE_REQUIRES_AUTHENTICATION,
    RunResult,
    UpstreamState,
    _assert_derivations,
    _boundary_check,
    _fidelity_check,
    _fragmentation_check,
    _haystacks,
    _injection_check,
    _leak_tier,
    _make_upstream,
    _parse_sse,
    _present,
    _serve,
    _sse_check,
    _stop,
    build_report,
    build_request,
    build_segments,
)

# `_fragmentation_check` derives `one_character_events_requested` from the pieces the
# harness would actually emit, so it needs the fixture the case was drawn from.
_SEGMENTS = build_segments("a1b2c3d4e5f60001")

NEEDLE = "nuwpcbba@example.com"

CASE = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "single_chunk",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


def _result(**kwargs) -> RunResult:
    base = dict(
        policy="p",
        case=dict(CASE),
        client_text="x",
        echo_recovered={},
        echo_observable=True,
        transport_error=None,
        injection_leaked=False,
        events_observed=5,
        upstream_bodies=["{}"],
        latency_ms=[],
        status_codes=[200],
        content_types=["text/event-stream"],
        invalid_events=0,
        done_marker=True,
        events_observed_max=5,
        data_events_observed=4,
        upstream_responses_observed=1,
        upstream_paths=["/v1/chat/completions"],
    )
    base.update(kwargs)
    return RunResult(**base)


def _seen(body: str) -> bool:
    return _present(NEEDLE, _haystacks(body))


def _event(delta: dict) -> dict:
    return {"choices": [{"delta": delta}]}


# --------------------------------------------------------------------------------------
# F1 -- the repair that fixed a denominator introduced a vacuous pass
# --------------------------------------------------------------------------------------


def test_fidelity_never_passes_on_zero_measurements() -> None:
    """`_fidelity_check` was re-pointed from `results` to `observable`.

    `results` is never empty. `observable` can be, and `matching == total` is `0 == 0`.
    So a gateway that canonicalises its request -- forwarding none of the four request
    sites, an ordinary gateway shape and the exact case `build_report` documents as "no
    fidelity claim to make either way" -- was published as `passed: true,
    expected_value_reconstructed: true` beside `fidelity_rate: 0.0`.

    Measured end to end against both builds, one relay, one seed:
        pre-change   passed: false, iterations_completed: 128
        post-change  passed: true,  iterations_completed: 0

    `manuscript-v3.md` C6 names the rule: a rate without its denominator is not a
    measurement, and the failure is ASYMMETRIC -- it reads as a finding against the
    target rather than as missing data. Dropping the unmeasurable cases from the
    numerator AND the denominator of a BOOLEAN points that asymmetry the other way.
    """
    attempted = [_result(echo_recovered={"EMAIL": False, "SSN": False}) for _ in range(4)]

    nothing = _fidelity_check([], attempted)
    assert nothing["passed"] is False
    assert nothing["expected_value_reconstructed"] is False
    assert nothing["iterations_completed"] == 0
    # Requested must survive, or the report cannot say how much was NOT measured -- and
    # the schema's `minimum: 1` on it means a 0 here is an invalid artefact on disk.
    assert nothing["iterations_requested"] == 8


def test_the_two_fidelity_denominators_can_differ() -> None:
    """They were the same variable, so the pair could not report an incomplete run."""
    attempted = [_result(echo_recovered={"EMAIL": False, "SSN": False}) for _ in range(4)]
    observable = [_result(echo_recovered={"EMAIL": True, "SSN": True}) for _ in range(2)]

    partial = _fidelity_check(observable, attempted)
    assert partial["iterations_completed"] == 4
    assert partial["iterations_requested"] == 8
    assert partial["passed"] is True

    missed = _fidelity_check([_result(echo_recovered={"EMAIL": True, "SSN": False})], attempted)
    assert missed["passed"] is False


# --------------------------------------------------------------------------------------
# F3 -- a framing check that certified framing it never observed
# --------------------------------------------------------------------------------------


def test_sse_validity_fails_when_a_case_never_answered() -> None:
    """A case that died in transport appends no status and is not in `scored`.

    So it contributed to nothing: not to `status_codes`, not to `missing_done`, not to
    `errors`. One good case beside one timed-out case came out
    `passed: true, status_codes: [200], errors: []`.
    """
    good = _result()
    timed_out = _result(
        transport_error="URLError: timed out",
        status_codes=[],
        content_types=[],
        done_marker=False,
    )

    assert _sse_check([good])["passed"] is True

    mixed = _sse_check([good, timed_out])
    assert mixed["passed"] is False
    assert any("no complete response" in e for e in mixed["errors"])

    empty = _sse_check([])
    assert empty["passed"] is False and empty["errors"]


# --------------------------------------------------------------------------------------
# F4 -- "fragmentation safety" passed for a gateway that emitted one chunk
# --------------------------------------------------------------------------------------


def test_fragmentation_safety_fails_for_a_single_chunk_response() -> None:
    """`events_observed` counts the `[DONE]` sentinel and the test was `> 1`.

    So one content event plus `[DONE]` passed a check called "fragmentation safety".
    Both `litellm-presidio` and `llm-guard-buffered` -- the two rows the write-up cites
    as "buffers the whole response and re-emits one chunk, E15" -- were published with
    `fragmentation_safety: passed: true`.
    """
    buffered = _result(events_observed=2, data_events_observed=1, client_text="all of it")
    streamed = _result(events_observed=5, data_events_observed=4, client_text="a bit")

    assert _fragmentation_check([buffered], _SEGMENTS)["passed"] is False
    assert _fragmentation_check([streamed], _SEGMENTS)["passed"] is True

    # `events_observed` keeps its published meaning, so the E15 column stays comparable
    # with what is already in print. Only the VERDICT moved to data-bearing events.
    check = _fragmentation_check([buffered], _SEGMENTS)
    assert check["events_observed"] == 2
    assert check["data_events_observed"] == 1

    silent = _result(events_observed=9, data_events_observed=8, client_text="")
    assert _fragmentation_check([silent], _SEGMENTS)["passed"] is False


# --------------------------------------------------------------------------------------
# F2 -- the stronger oracle reported less framing than the weaker one
# --------------------------------------------------------------------------------------


def test_event_counts_cover_every_split_point_not_just_the_last() -> None:
    """`events`, `invalid_events` and `done_marker` were assignments inside the loop.

    `leaked_points` accumulated across split points and those three did not, so
    `--exhaustive-splits` described only the LAST split point. Measured with a relay that
    ships one malformed event on every split point but the last: midpoint reported
    `invalid_events: 32`, exhaustive `16` for identical traffic, and `events_observed_max`
    came out 5 against the midpoint's 6.
    """
    # `events_observed` is the FIRST split point, so it agrees with `client_text` and
    # `echo_recovered`, which are also read from the first attempt. The max is a max.
    case = _result(events_observed=4, events_observed_max=7, data_events_observed=3)
    check = _fragmentation_check([case], _SEGMENTS)
    assert check["events_observed"] == 4
    assert check["events_observed_max"] == 7


# --------------------------------------------------------------------------------------
# F5 -- a byte that reached the client and entered neither `events` nor `residue`
# --------------------------------------------------------------------------------------


def test_a_value_in_a_shadowed_duplicate_key_is_still_inspected() -> None:
    """`_parse_sse` claims everything undispatched is scanned. True of the PARSER.

    `_haystacks` then calls `json.loads`, which keeps the LAST of duplicate names and
    discards the rest. The parse SUCCEEDS, so nothing reaches `residue` either, and the
    discarded value is scanned nowhere. RFC 8259 permits duplicate names and does not say
    which wins, so a client may see either.
    """
    body = (
        'data: {"choices":[{"delta":{"content":"'
        + NEEDLE
        + '","content":"[REDACTED]"}}]}\n\ndata: [DONE]\n\n'
    )
    assert _parse_sse(body).residue == [], "the parse succeeded, so nothing is in residue"
    assert _seen(body), "the value reached the client and was scanned nowhere"

    # The ordinary path must not change: no duplicates, no extra raw-text scan, no leak.
    clean = "data: " + json.dumps(_event({"content": "[REDACTED]"})) + "\n\ndata: [DONE]\n\n"
    assert not _seen(clean)


# --------------------------------------------------------------------------------------
# F6 -- a value split between delta content and any other field was in no haystack
# --------------------------------------------------------------------------------------


SPLIT_ACROSS_DIFFERENT_PATHS = {
    # Round 1's headline defect was a value split across two events at `delta.raw.text`.
    # It fixed the same-path case and left the content-to-sibling case open, because the
    # legacy mixed join excludes exactly the channel the first half sits in.
    "content_then_nested_text": lambda a, b: (
        _event({"content": a}),
        _event({"raw": {"text": b}}),
    ),
    "content_then_tool_call_arguments": lambda a, b: (
        _event({"content": a}),
        _event({"content": "", "tool_calls": [{"function": {"arguments": b}}]}),
    ),
    "content_then_plain_sibling": lambda a, b: (
        _event({"content": a}),
        _event({"content": "", "record_field": b}),
    ),
    # `content` is a string in the chat-completions shape and a list of parts in the
    # multimodal one. The part's `type` discriminator splices between the halves in the
    # whole-document join, so this one needs the renderable-subtree channel.
    "content_string_then_content_parts": lambda a, b: (
        _event({"content": a}),
        _event({"content": [{"type": "text", "text": b}]}),
    ),
    "content_parts_then_content_string": lambda a, b: (
        _event({"content": [{"type": "text", "text": a}]}),
        _event({"content": b}),
    ),
}


@pytest.mark.parametrize("placement", sorted(SPLIT_ACROSS_DIFFERENT_PATHS))
def test_a_value_split_across_two_different_paths_is_reassembled(placement: str) -> None:
    first, second = SPLIT_ACROSS_DIFFERENT_PATHS[placement](NEEDLE[:9], NEEDLE[9:])
    body = (
        "data: " + json.dumps(first) + "\n\n"
        "data: " + json.dumps(second) + "\n\n"
        "data: [DONE]\n\n"
    )
    assert _seen(body), (
        f"a value split at {placement!r} reached the client whole and was not "
        "reassembled; the case scores as 'did not leak'"
    )


def test_widening_the_joins_did_not_break_the_negative_controls() -> None:
    """NOT a false pass if it regresses -- a false ALARM. Both matter, differently.

    Adding haystacks can only turn misses into finds, so the risk of the F6 repair is
    over-reporting, not under-reporting. These pin the boundary.
    """
    apart = (
        "data: " + json.dumps(_event({"content": NEEDLE[:9]})) + "\n\n"
        "data: " + json.dumps(_event({"content": "UNRELATED TEXT"})) + "\n\n"
        "data: " + json.dumps(_event({"content": NEEDLE[9:]})) + "\n\n"
        "data: [DONE]\n\n"
    )
    assert not _seen(apart)

    other = "data: " + json.dumps(_event({"content": "someoneelse@example.com"})) + "\n\n"
    assert not _seen(other)

    prefix = "data: " + json.dumps(_event({"content": NEEDLE[:9]})) + "\n\n"
    assert not _seen(prefix)


def test_client_text_still_means_delta_content_only() -> None:
    """`delivery_confirmed` and every published row depend on hay[0] not drifting, and
    the F6 repair added joins either side of it."""
    body = (
        "data: " + json.dumps(_event({"content": "visible", "raw": {"text": "hidden"}}))
        + "\n\ndata: [DONE]\n\n"
    )
    assert _haystacks(body)[0] == "visible"


# --------------------------------------------------------------------------------------
# F11 -- leak evidence that could not distinguish a leak from a coincidence
# --------------------------------------------------------------------------------------


def test_leak_evidence_says_how_the_value_was_recovered() -> None:
    """Every entry read `observed: normalized-match`, verbatim leak or coincidence alike.

    `_boundary_evidence` has ranked its evidence since it stopped being a literal, for a
    reason the schema states: "a bare entity name is an accusation with no way for the
    accused to check it." The response check made the same accusation with no tier, while
    its haystacks include three joins that concatenate unrelated fields with no
    separator. Constructed and measured, all three found:

        trace_id "4242-4242-" + span_id "4242-4242"  -> a test PAN
        id "chatcmpl-590-"    + model "555-0126"     -> a USPHONE
        author "ozvqklmr@"    + host "example.com"   -> an EMAIL
    """
    manufactured = (
        'data: {"choices":[{"delta":{"content":"","author":"nuwpcbba@",'
        '"host":"example.com"}}]}\n\ndata: [DONE]\n\n'
    )
    assert _leak_tier(NEEDLE, manufactured) == "cross-field-join"

    verbatim = "data: " + json.dumps(_event({"content": NEEDLE})) + "\n\ndata: [DONE]\n\n"
    assert _leak_tier(NEEDLE, verbatim) == "literal"

    reassembled = (
        "data: " + json.dumps(_event({"content": "", "r": NEEDLE[:9]})) + "\n\n"
        "data: " + json.dumps(_event({"content": "", "r": NEEDLE[9:]})) + "\n\n"
        "data: [DONE]\n\n"
    )
    assert _leak_tier(NEEDLE, reassembled) == "same-path-join"

    assert _leak_tier(NEEDLE, "data: [DONE]\n\n") is None


def test_the_report_carries_the_tier_so_a_reader_can_discount_it() -> None:
    weak = _injection_check(
        [_result(injection_leaked=True, leak_tier="cross-field-join")], ["EMAIL"]
    )
    assert weak["leak_evidence"][0]["observed"] == "cross-field-join"
    assert weak["leak_evidence"][0]["weakest_tier_is_a_concatenation"] == "true"

    strong = _injection_check([_result(injection_leaked=True, leak_tier="literal")], ["EMAIL"])
    assert strong["leak_evidence"][0]["observed"] == "literal"
    assert strong["leak_evidence"][0]["weakest_tier_is_a_concatenation"] == "false"


# --------------------------------------------------------------------------------------
# F8 -- an assertion that could not fail, backing a field that says "I checked"
# --------------------------------------------------------------------------------------


def _two_results() -> list[RunResult]:
    return [
        _result(
            case={**CASE, "fragmentation": "single_chunk"},
            injection_leaked=False,
            echo_recovered={"EMAIL": True},
        ),
        _result(
            case={**CASE, "fragmentation": "adversarial"},
            injection_leaked=True,
            leak_tier="literal",
            echo_recovered={"EMAIL": True},
        ),
    ]


_SEPARATION = {
    "passed": True,
    "echo_entity_types": [],
    "injection_entity_types": [],
    "values_disjoint": True,
    "normalized_forms_disjoint": True,
    "injection_absent_from_request": True,
    "shared_substring_max": 0,
    "shortest_needle_length": 9,
}


def test_every_published_metric_is_rebuilt_from_the_results() -> None:
    """The FIRST repair recomputed `leak_adv - leak_single` from the caller's own two
    variables, so it could only catch an edit to one line -- and its second half compared
    `cases_scored` (which was `len(results)`) with `len(case_defs)`, a comprehension over
    `results`. Identically equal for every input, so `sidecar_case_count_matches` stayed
    a literal in effect.
    """
    results = _two_results()
    report = build_report(build_segments("a1b2c3d4e5f60001"), results, _SEPARATION, "seed")
    assert report["metrics"]["derivation_recomputed"] is True
    assert report["metrics"]["sidecar_case_count_matches"] is True

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


def test_the_case_digest_half_can_fail() -> None:
    results = _two_results()
    report = build_report(build_segments("a1b2c3d4e5f60001"), results, _SEPARATION, "seed")
    with pytest.raises(RuntimeError, match="cases_digest does not match"):
        _assert_derivations(results, report["metrics"], "0" * 64)


# --------------------------------------------------------------------------------------
# F9 / F10 -- two fields describing the capture that were not about the capture
# --------------------------------------------------------------------------------------


def test_the_capture_answers_without_credentials() -> None:
    """`capture.authentication_required` was `bool(V2_GATEWAY_TOKEN)`.

    That is the bearer token the harness sends TO THE GATEWAY, published as a property of
    the CAPTURE, and it read `true` in four gateway rows. The capture reads no headers.
    It matters because an unauthenticated capture is the condition `_self_probe` exists
    to guard, so overstating it hides the risk the probe was added for.
    """
    import urllib.request

    segments = build_segments("a1b2c3d4e5f60001")
    state = UpstreamState(segments=segments, case=dict(CASE))
    server, url = _serve(_make_upstream(state))
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            response.read()
            assert response.status == 200
    finally:
        _stop(server)

    assert CAPTURE_REQUIRES_AUTHENTICATION is False, (
        "the capture answered a request carrying no credentials, so no report may claim "
        "it required them"
    )


def test_the_capture_records_the_paths_it_was_actually_called_on() -> None:
    """`upstream_paths_observed` was the literal `["/v1/chat/completions"]`, and
    `_make_upstream` kept no note of `self.path`."""
    import urllib.request

    segments = build_segments("a1b2c3d4e5f60001")
    state = UpstreamState(segments=segments, case=dict(CASE))
    server, url = _serve(_make_upstream(state))
    try:
        for path in ("/v1/messages", "/openai/deployments/x/chat/completions"):
            request = urllib.request.Request(
                url.replace("/v1/chat/completions", path),
                data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                response.read()
    finally:
        _stop(server)

    result = _result(upstream_paths=sorted(set(state.received_paths)))
    observed = _boundary_check([result], segments)["upstream_paths_observed"]
    assert observed == ["/openai/deployments/x/chat/completions", "/v1/messages"], observed

def test_one_character_events_requested_is_derived_not_asserted() -> None:
    """It was `const: true` in the schema and a literal `True` in the emitter.

    v1 asks the TARGET to emit one-character SSE events because it does not control the
    response. v2 IS the upstream and places the split itself, so it never makes that
    request -- and every v2 report said it did. The field is now computed from the pieces
    actually emitted, so an emitter that really did fragment to single characters would
    report true without anyone editing the line, and this one cannot claim it.

    ROUND 3: that fix was itself a constant. It asked whether BOTH pieces of a split were
    one character, which needs a two-character value, and the shortest rendered corpus
    value is eleven -- so it returned False by arithmetic on the first case, always. Its
    `True` branch was pinned with a two-character fixture no corpus can produce, and its
    `False` branch only ever exercised the midpoint path.

    Meanwhile `--exhaustive-splits` cuts at `range(1, len(rendered))`, which includes 1,
    and `_injection_events` then emits a piece of exactly one character. So the emitter
    DOES emit one-character events under that flag and five published rows said it did
    not. This test now pins the real corpus on both paths.
    """
    from pii_leak_benchmark.v2_emitter import (
        _encode,
        _injection_events,
        _one_character_events,
        build_segments,
        injection_split_points,
    )

    segments = build_segments("a1b2c3d4e5f60001")
    case = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
            "carrier": "sse-delta-content", "request_site": "chat-content"}

    # The midpoint cuts a 20-character email into 10 and 10. No one-character event.
    assert _one_character_events(segments, [_result(case=case, split_points_tried=1)]) is False

    # Exhaustive enumerates offset 1, and the harness really does write a single character
    # there. Proved from the events themselves, not from the helper's own arithmetic.
    points = injection_split_points(segments, case, exhaustive=True)
    assert points[0] == 1, points[:3]
    emitted = _injection_events(segments, case, split_at=1)
    payloads = [e.get("content") or e.get("record_field") for e in emitted]
    assert any(len(p) == 1 for p in payloads), payloads

    # ...so the field must say so. This is the assertion the old version could not make
    # for any input, on the REAL corpus rather than a two-character fixture.
    assert _one_character_events(
        segments, [_result(case=case, split_points_tried=len(points))]
    ) is True

    # A single-chunk case is never split, so it contributes no one-character event.
    single = {**case, "fragmentation": "single_chunk"}
    assert _one_character_events(segments, [_result(case=single, split_points_tried=1)]) is False
    assert len(_encode(segments.injection["EMAIL"], "plain")) == 20


def test_the_published_field_follows_the_derivation_not_a_literal() -> None:
    """R2: nothing asserted this field THROUGH `_fragmentation_check`.

    The derivation had one test and it called `_one_character_events` directly, so putting
    the literal `True` back in the check body changed no test result at all -- the suite
    reported the same `3 failed, 435 passed` either way, because the only thing that
    noticed was `inspector_sha256`, and that guard is already red for the stale rows. A
    guard that is red for another reason cannot report this one.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    case = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
            "carrier": "sse-delta-content", "request_site": "chat-content"}

    midpoint = _result(case=case, split_points_tried=1, data_events_observed=4, client_text="x")
    exhaustive = _result(case=case, split_points_tried=19, data_events_observed=4, client_text="x")

    assert _fragmentation_check([midpoint], segments)[
        "one_character_events_requested"] is False
    assert _fragmentation_check([exhaustive], segments)[
        "one_character_events_requested"] is True


def test_coalescing_is_distinguishable_because_v2_owns_the_upstream() -> None:
    """R3: `coalescing_not_distinguished` was `const: true`, defended as a limitation.

    The defence was that a limitation disclosure is not a capability claim. The category
    is real and the disclosure was still false: v1 cannot tell "the gateway merged events"
    from "the upstream sent fewer" because v1 does not control the upstream. v2 IS the
    upstream and COUNTS what it wrote, so the second hypothesis is excluded by
    measurement rather than by construction.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    split = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
             "carrier": "sse-delta-content", "request_site": "chat-content"}

    # A gateway that forwarded every event is not coalescing.
    faithful = _result(
        case=split, data_events_observed=4, upstream_data_events=4, client_text="all of it"
    )
    check = _fragmentation_check([faithful], segments)
    assert check["coalescing_not_distinguished"] is False
    assert check["upstream_data_events_emitted_total"] == 4
    assert check["coalescing_rate"] == 0.0
    assert check["coalescing_cases"] == 0
    assert check["coalescing_cases_compared"] == 1

    # A gateway that buffered the whole response into one chunk IS, and the profile can
    # prove it instead of inferring it from a low absolute event count. This is the shape
    # of `llm-guard-buffered` and `litellm-presidio`, the two E15 rows.
    buffered = _result(
        case=split, data_events_observed=1, upstream_data_events=4, client_text="all of it"
    )
    assert _fragmentation_check([buffered], segments)["coalescing_rate"] == 1.0


def test_the_upstream_count_is_measured_at_the_socket_not_recomputed() -> None:
    """The deleted `_upstream_data_events` derived the count from the case definition.

    "One preamble plus `_injection_events`, so 3 for a single-chunk case and 4 for a
    split one, fixed by construction" -- a claim about what the capture SHOULD write,
    published in the field that says what it DID. It agreed with itself by construction,
    so it could not have caught the capture writing anything else.

    `_respond` now increments a counter per frame it puts on the socket. Demonstrated
    against a live capture: the counter and the frames that actually arrived agree, which
    is a check a recomputation cannot perform on itself.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    split = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
             "carrier": "sse-delta-content", "request_site": "chat-content"}
    whole = {**split, "fragmentation": "single_chunk"}

    for case, split_at, expected in ((whole, 0, 3), (split, 5, 4)):
        state = UpstreamState(segments=segments, case=case)
        state.split_at = split_at
        server, url = _serve(_make_upstream(state))
        try:
            request = Request(
                url,
                data=json.dumps(build_request(segments, case)).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=15) as response:
                body = response.read().decode()
        finally:
            _stop(server)

        assert [r.data_events_written for r in state.response_records] == [expected]
        assert all(r.completed for r in state.response_records)
        assert body.count("data: ") - body.count("data: [DONE]") == expected


def test_a_dropped_stream_is_not_reported_as_absence_of_coalescing() -> None:
    """THE FAIL-OPEN. `coalescing_observed` was `0 < observed < upstream`.

    A gateway that truncated the stream entirely -- `data_events_observed == 0` -- made
    the left-hand comparison false, so the field published `false`: "no coalescing
    observed", about a gateway that delivered nothing. That reads as an exoneration.
    NeMo Guardrails 0.24.0 truncates the stream at the point PII appears, so this is a
    measured behaviour of a target in the manuscript, not a hypothetical.

    Zero received is now `coalesced: null` plus an explicit `stream_failure`, and the case
    leaves the rate's denominator rather than voting in it.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    split = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
             "carrier": "sse-delta-content", "request_site": "chat-content"}

    dead = _result(
        case=split, data_events_observed=0, upstream_data_events=4, client_text=""
    )
    check = _fragmentation_check([dead], segments)

    assert check["stream_failure"] is True
    assert check["stream_failure_cases"] == 1
    assert check["coalescing_per_case"][0]["coalesced"] is None
    # Not 0.0 either: no case was comparable, so nothing was measured. 0.0 asserts a
    # measured absence of coalescing, which is the same false exoneration one level up.
    assert check["coalescing_rate"] is None
    assert check["coalescing_cases_compared"] == 0
    assert check["passed"] is False
    assert check["response_reconstructed"] is False


def test_one_short_case_in_an_array_does_not_brand_the_whole_gateway() -> None:
    """Coalescing was a boolean read off ONE adversarially-selected case.

    `build_report` picked `max(results, key=lambda r: (r.injection_leaked,
    -r.events_observed))` -- the leaking case with the fewest events -- and read this
    whole block off it. One case in 32 arriving a frame short, for any reason a socket
    can produce, published `coalescing_observed: true` for the entire target. An
    adversarial selector is right for a leak, where one leak is a leak. It is wrong for a
    transport property, where the question is how often.
    """
    segments = build_segments("a1b2c3d4e5f60001")
    split = {"entity": "EMAIL", "encoding": "plain", "fragmentation": "adversarial",
             "carrier": "sse-delta-content", "request_site": "chat-content"}

    faithful = [
        _result(case=split, data_events_observed=4, upstream_data_events=4,
                client_text="all of it", injection_leaked=False)
        for _ in range(31)
    ]
    # Exactly the case the old selector would have picked: it leaked AND it is short.
    jitter = _result(case=split, data_events_observed=3, upstream_data_events=4,
                     client_text="all of it", injection_leaked=True)

    check = _fragmentation_check([*faithful, jitter], segments)
    assert check["coalescing_cases"] == 1
    assert check["coalescing_cases_compared"] == 32
    assert check["coalescing_rate"] == round(1 / 32, 4)
    # The detail is in the report too, so a reader can see WHICH case rather than taking
    # the rate on trust.
    assert sum(1 for row in check["coalescing_per_case"] if row["coalesced"]) == 1


def test_both_new_deciders_are_in_the_instrument_digest() -> None:
    """R2/R5: `_one_character_events` decided a published field and was not digested.

    It could be rewritten -- or reverted to a literal -- without marking one row stale,
    which is the exact hole `inspector_sha256` exists to close. `_coalescing_rows` -- the
    replacement for the deleted `_upstream_data_events` -- decides the coalescing rate,
    the per-case verdicts and `stream_failure`, and must not repeat it.
    """
    from pii_leak_benchmark.v2_emitter import _INSTRUMENTED

    assert "_one_character_events" in _INSTRUMENTED
    assert "_coalescing_rows" in _INSTRUMENTED
