"""Partial emission of a later-masked value: the response-path gap the leak tiers miss.

`_leak_tier` reports a leak only when the COMPLETE injected value can be recovered from
what the client received. A gateway that forwards `nuwpcbba@exa` in one delta, then
recognises the value across the chunk boundary and sends `[REDACTED]` for the rest,
delivers `nuwpcbba@exa[REDACTED]`. The whole needle is absent, no tier matches, and the
published scorer calls the case contained. The client nevertheless received twelve
characters of a live address. This is the fixed-carryover failure: the carryover catches
the value, but only after its first fragment is already on the wire.

The published scorer is pinned (its `inspector_sha256` is carried by every published
row), so the gap is closed by a SEPARATE, separately-versioned instrument,
`pii_leak_benchmark.partial_emission`, and reported as its own tier. The first two tests
below pin the gap in the published scorer; they pass before and after the fix, because
changing that scorer would silently re-score every published row. Everything after them
exercises the new instrument.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark import v2_emitter as v2  # noqa: E402

NEEDLE = "nuwpcbba@example.com"
ECHO_EMAIL = "zqxjvkwy@example.com"

EMAIL_CASE = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "adversarial",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


def _sse(*contents: str) -> str:
    return (
        "".join("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) + "\n\n" for c in contents)
        + "data: [DONE]\n\n"
    )


PREFIX_THEN_MASK = _sse("You sent: " + ECHO_EMAIL + "\n", "Reference record: ", "nuwpcbba@exa", "[REDACTED]")


class CarryoverAfterEmit(v2.Policy):
    """The fixed-carryover design: detects across the boundary, but only after emitting.

    Each delta is forwarded as soon as it arrives (after chunk-local redaction). A short
    carryover of the previous tail is joined to the next delta so a value split across
    the boundary IS recognised -- and the remainder is replaced with a mask. The first
    fragment has already gone.
    """

    name = "carryover-after-emit"
    rehydrates = True
    redacts = True

    def __init__(self, vault: dict[str, str]) -> None:
        super().__init__(vault)
        self._tail = ""

    def feed(self, delta: str) -> str:
        joined = self._tail + delta
        boundary = len(self._tail)
        out = None
        for _entity, pattern in v2._DETECTORS:
            for match in pattern.finditer(joined):
                if match.start() < boundary < match.end():
                    out = "[REDACTED]" + v2._redact_then_rehydrate(delta[match.end() - boundary :], self.vault)
                    break
            if out is not None:
                break
        if out is None:
            out = v2._redact_then_rehydrate(delta, self.vault)
        self._tail = joined[-32:]
        return out


@pytest.fixture()
def carryover_policy(monkeypatch):
    monkeypatch.setitem(v2.POLICIES, CarryoverAfterEmit.name, CarryoverAfterEmit)
    return CarryoverAfterEmit.name


# ------------------------------------------------------------- the gap, pinned as published


def test_the_published_scorer_does_not_see_a_prefix_then_mask_stream() -> None:
    """The defect, stated as the published scorer's actual behaviour.

    This passes before AND after the fix on purpose: `_leak_tier` is inside the published
    digest, and making it see partial emission would re-score every row on the results
    wall under an assertion none of them was produced with.
    """
    assert NEEDLE not in "".join(v2._haystacks(PREFIX_THEN_MASK))
    assert v2._leak_tier(NEEDLE, PREFIX_THEN_MASK) is None


def test_the_published_harness_scores_the_carryover_policy_as_contained(carryover_policy) -> None:
    """End to end over loopback HTTP: the case is reported as no leak."""
    segments = v2.build_segments("a1b2c3d4e5f60001")
    result = v2.run_case(segments, carryover_policy, EMAIL_CASE, iterations=1)
    needle = segments.injection["EMAIL"]
    assert result.transport_error is None
    assert result.injection_leaked is False
    # ...while the client did receive the first half of the value.
    assert needle[: len(needle) // 2] in result.client_text


# ------------------------------------------------------------------- the new instrument


def test_prefix_then_mask_is_scored_as_partial_emission() -> None:
    from pii_leak_benchmark import partial_emission as pe

    benign = [ECHO_EMAIL, "You sent: ", "Reference record: "]
    assert pe.partial_emission_tier(NEEDLE, PREFIX_THEN_MASK, benign) == "partial-emission"
    # Twelve characters of the needle arrived, none of them supplied by the client.
    assert pe.specific_run(NEEDLE, v2._haystack_groups(PREFIX_THEN_MASK)["same-path-join"], benign) >= len(
        "nuwpcbbaexa"
    )


def test_a_complete_leak_is_not_downgraded_to_partial() -> None:
    """A whole value is a stronger tier. The weaker one must not claim it."""
    from pii_leak_benchmark import partial_emission as pe

    sse = _sse("Reference record: ", "nuwpcbba@exa", "mple.com")
    assert v2._leak_tier(NEEDLE, sse) == "same-path-join"
    assert pe.partial_emission_tier(NEEDLE, sse, []) is None


def test_a_clean_mask_is_not_partial_emission() -> None:
    from pii_leak_benchmark import partial_emission as pe

    sse = _sse("You sent: " + ECHO_EMAIL + "\n", "Reference record: ", "[REDACTED]")
    benign = [ECHO_EMAIL, "You sent: ", "Reference record: "]
    assert pe.partial_emission_tier(NEEDLE, sse, benign) is None


def test_text_the_client_supplied_is_not_evidence() -> None:
    """The benign collision the raw run cannot tell apart from a leak.

    Echo and injection emails share `@example.com`, so the raw longest run of the needle
    in a CORRECT response is twelve characters (normalised: `examplecom`, ten). Counting
    it would flag every correct gateway. Only needle text the client did not itself send
    counts.
    """
    from pii_leak_benchmark import http_profile
    from pii_leak_benchmark import partial_emission as pe

    sse = _sse("You sent: " + ECHO_EMAIL + "\n", "Reference record: ", "[REDACTED]")
    haystacks = v2._haystack_groups(sse)["same-path-join"]
    raw = http_profile._needle_proximity(
        http_profile._normalize(NEEDLE), tuple(http_profile._normalize(h) for h in haystacks)
    )
    assert raw >= len("examplecom"), "precondition: the benign collision exists"
    assert pe.specific_run(NEEDLE, haystacks, [ECHO_EMAIL, "You sent: ", "Reference record: "]) == 0


def test_in_process_carryover_policy_is_flagged_by_both_oracles(carryover_policy) -> None:
    """Harness owns the policy: the exact chunking-invariance oracle applies."""
    from pii_leak_benchmark import partial_emission as pe

    segments = v2.build_segments("a1b2c3d4e5f60001")
    row = pe.score_case(segments, carryover_policy, EMAIL_CASE)
    assert row.transport_error is None
    assert row.oracle == "chunking-invariance"
    assert row.partial_emission is True
    assert row.partitions_partial == row.partitions_tried == 1
    assert row.invariance_violations == 1
    assert row.complete_leak_partitions == 0
    # The threshold detector used for external gateways agrees with the exact oracle.
    assert row.threshold_partitions == 1
    assert row.threshold_only_partitions == 0
    assert row.longest_specific_run >= pe.MIN_SPECIFIC_RUN


@pytest.mark.parametrize("policy", ["bounded-retention", "retention-plus-decoding"])
def test_a_correct_retaining_policy_is_chunking_invariant_at_every_split(policy: str) -> None:
    """The assertion must not flag a policy that is actually correct.

    Every two-part split of every covering-array case: byte-identical output to the
    policy run over the whole stream at once, and no partial emission.
    """
    from pii_leak_benchmark import partial_emission as pe

    segments = v2.build_segments("a1b2c3d4e5f60001")
    for case in v2.covering_array():
        if case["fragmentation"] != "adversarial":
            continue
        row = pe.score_case(segments, policy, case, oracle="exhaustive-2-part")
        assert row.transport_error is None
        assert row.invariance_violations == 0, (policy, case)
        assert row.partitions_partial == 0, (policy, case)
        assert row.threshold_partitions == 0, (policy, case)
        assert row.threshold_only_partitions == 0, (policy, case)


def test_chunk_local_violates_invariance_but_its_leak_is_complete_not_partial() -> None:
    """chunk-local leaks the whole value. That is the stronger tier, not this one."""
    from pii_leak_benchmark import partial_emission as pe

    segments = v2.build_segments("a1b2c3d4e5f60001")
    row = pe.score_case(segments, "chunk-local", EMAIL_CASE)
    assert row.invariance_violations == 1
    assert row.complete_leak_partitions == 1
    assert row.partial_emission is False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_external_gateway_path_uses_the_threshold_detector(carryover_policy) -> None:
    """`--gateway-url`: the harness cannot run the whole-string filter, only look.

    The carryover policy is hosted as if it were someone else's proxy, preconfigured to
    use a fixed capture port.
    """
    from pii_leak_benchmark import partial_emission as pe

    port = _free_port()
    gateway, url = v2._serve(v2._make_gateway(f"http://127.0.0.1:{port}/v1/chat/completions", carryover_policy))
    try:
        segments = v2.build_segments("a1b2c3d4e5f60001")
        row = pe.score_case(segments, "some-gateway", EMAIL_CASE, gateway_url=url, upstream_port=port)
    finally:
        v2._stop(gateway)
    assert row.transport_error is None
    assert row.oracle == "needle-run-threshold"
    assert row.invariance_violations is None
    assert row.threshold_only_partitions is None
    assert row.partial_emission is True


def test_the_report_keeps_the_tier_out_of_the_leak_rate(carryover_policy) -> None:
    from pii_leak_benchmark import partial_emission as pe

    segments = v2.build_segments("a1b2c3d4e5f60001")
    # Each entity in both arms keeps this fast; the CLI test runs the whole array.
    cases = [
        dict(EMAIL_CASE, entity=entity, fragmentation=arm)
        for entity in v2.AXES["entity"]
        for arm in ("single_chunk", "adversarial")
    ]
    rows = [pe.score_case(segments, carryover_policy, case) for case in cases]
    report = pe.build_partial_emission_report(rows, "a1b2c3d4e5f60001")
    assert report["schema"] == pe.PARTIAL_EMISSION_SCHEMA_ID
    assert report["tier"]["name"] == "partial-emission"
    assert report["tier"]["counted_in_leak_rate"] is False
    assert report["tier"]["rank"] == v2._LEAK_TIER_RANK["partial-emission"]
    assert report["tier"]["rank"] > v2._LEAK_TIER_RANK["cross-field-join"]
    assert report["metrics"]["cases_partial_emission"] > 0
    assert report["passed"] is False
    assert report["values_published"] is False
    # Unfragmented, the carryover design is correct; cut, every entity is partially emitted.
    assert report["metrics"]["partial_emission_rate"]["single_chunk"] == 0.0
    assert report["metrics"]["partial_emission_rate"]["adversarial"] == 1.0
    assert report["partial_emission_entity_types"] == sorted(v2.AXES["entity"])
    assert report["metrics"]["cases_complete_leak"] == 0
    # No specimen value anywhere in the artefact.
    text = json.dumps(report)
    for value in [*segments.injection.values(), *segments.echo.values()]:
        assert value not in text
    assert report["instrument"]["base_inspector_sha256"] == v2.inspector_digest()


def test_the_published_inspector_digest_is_not_moved() -> None:
    """The whole design constraint, restated where the new instrument lives."""
    assert v2.inspector_digest() == "94262e29a492ab6a"
    assert "partial-emission" not in v2._HAYSTACK_TIERS


def test_a_fixture_shaped_substitute_is_not_a_fragment() -> None:
    """A gateway that replaces a card with another published test PAN is not leaking.

    `4111-1111-1111-1111` and `6011-1111-1111-1117` share thirteen consecutive `1`s. A
    whole, well-formed value that is not the needle is a substitute; only what lies
    outside such values can be a fragment.
    """
    from pii_leak_benchmark import http_profile
    from pii_leak_benchmark import partial_emission as pe

    needle = "4111-1111-1111-1111"
    sse = _sse("Reference record: ", "6011-1111-1111-1117")
    haystacks = pe.response_haystacks(sse)
    raw = http_profile._needle_proximity(
        http_profile._normalize(needle), tuple(http_profile._normalize(h) for h in haystacks)
    )
    assert raw >= 13, "precondition: the substitute shares a long run with the needle"
    assert pe.specific_run(needle, haystacks, ["Reference record: "]) == 0
    # A fragment of the needle followed by a mask is not a well-formed value and survives.
    fragment = _sse("Reference record: ", "4111-1111-", "[REDACTED]")
    assert pe.partial_emission_tier(needle, fragment, ["Reference record: "]) == "partial-emission"


def test_the_chunk_envelope_does_not_set_the_verdict() -> None:
    """A ten-digit `created` timestamp must not be read as a fragment of an SSN."""
    from pii_leak_benchmark import partial_emission as pe

    needle = "907-31-4459"
    event = {
        "id": "chatcmpl-x",
        "object": "chat.completion.chunk",
        "created": 1907314459,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": "Reference record: [REDACTED]"}, "finish_reason": None}],
    }
    sse = "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
    assert v2._leak_tier(needle, sse) is not None, "precondition: the published scorer searches the envelope"
    envelope_only = dict(event, created=1790731445)
    sse = "data: " + json.dumps(envelope_only) + "\n\ndata: [DONE]\n\n"
    assert v2._leak_tier(needle, sse) is None
    assert pe.specific_run(needle, pe.response_haystacks(sse), []) == 0
    assert pe.partial_emission_tier(needle, sse, []) is None


def test_the_measured_benign_floor_is_below_the_threshold() -> None:
    """Re-run a slice of the measurement behind MIN_SPECIFIC_RUN.

    `benchmarks/partial_emission_threshold.py` is the full measurement. This slice keeps
    the threshold honest if the corpus, the detectors or the exclusions change.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))
    import partial_emission_threshold as measurement
    from pii_leak_benchmark import partial_emission as pe

    result = measurement._simulate_chunk((0, 4))
    detector = {k: v for k, v in result["top"].items() if k.endswith("|detector")}
    assert detector, "the measurement produced nothing"
    assert max(detector.values()) < pe.MIN_SPECIFIC_RUN, detector
    # And the raw run, which the exclusion exists to avoid, is well above it.
    raw = {k: v for k, v in result["top"].items() if k.endswith("|raw")}
    assert max(raw.values()) >= pe.MIN_SPECIFIC_RUN


def test_the_instrument_digest_moves_with_the_threshold(monkeypatch) -> None:
    from pii_leak_benchmark import partial_emission as pe

    before = pe.partial_emission_digest()
    monkeypatch.setattr(pe, "MIN_SPECIFIC_RUN", pe.MIN_SPECIFIC_RUN + 1)
    assert pe.partial_emission_digest() != before


@pytest.mark.slow
def test_the_cli_writes_the_sidecar_and_leaves_the_report_alone(tmp_path, carryover_policy) -> None:
    """`--partial-emission` adds an artefact; the v2 report is the one it always was."""
    from pii_leak_benchmark import partial_emission as pe

    code = v2.main(["--out", str(tmp_path), "--only", carryover_policy, "--seed", "a1b2c3d4e5f60001", "--partial-emission"])
    assert code == 0
    report = json.loads((tmp_path / f"{carryover_policy}.json").read_text(encoding="utf-8"))
    sidecar = json.loads((tmp_path / f"{carryover_policy}.partial-emission.json").read_text(encoding="utf-8"))
    assert report["schema"] == v2.SCHEMA_ID
    assert report["instrument"] == v2.instrument_block()
    assert "partial-emission" not in json.dumps(report)
    assert sidecar["schema"] == pe.PARTIAL_EMISSION_SCHEMA_ID
    assert sidecar["corpus"]["seed"] == report["corpus"]["seed"]
    assert sidecar["corpus"]["cases_digest"] == report["cases_digest"]
    assert sidecar["metrics"]["cases_partial_emission"] > 0
