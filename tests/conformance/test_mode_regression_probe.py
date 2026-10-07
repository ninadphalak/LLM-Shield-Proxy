"""The stream-mode probe: does it measure what it says, and does it disturb round 8?

The probe exists to answer one question the v2 profile structurally cannot ask, because
`v2_emitter.build_request` sends `"stream": True` unconditionally: does a response-path
privacy feature that works on a whole response stop working when the same request is
streamed?

Two things have to hold for its answer to be worth anything.

  1. It must score all three arms with the PUBLISHED inspector, or the arms are not
     comparable and the difference between them is an artifact of two scorers.
  2. It must not move `inspector_sha256`. Every published round-8 sweep carries that
     digest, and `test_results_are_comparable.py` marks a sweep stale the moment the
     current code's digest differs. Adding a non-streaming branch to `_make_upstream`
     would have turned the entire published tree stale for a three-row addendum.

The second one is why the probe has its own capture instead of teaching the emitter's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))
sys.path.insert(0, str(ROOT / "benchmarks"))

import mode_regression_probe as probe  # noqa: E402
from pii_leak_benchmark.v2_emitter import instrument_block  # noqa: E402

# The digest every round-8 report and sweep carries. Recorded in the manuscript, in
# `.llm/CONTEXT.md`, and in 168 schema reports.
ROUND_8_INSPECTOR = "94262e29a492ab6a"


def test_the_probe_does_not_move_the_published_inspector_digest() -> None:
    """The whole reason this module is not inside `v2_emitter`.

    If this fails, either an `_INSTRUMENTED` function changed or one was added, and every
    published round-8 sweep just became stale. That is a full evidence round, not a
    three-row addendum. Do not "fix" this by editing the constant.
    """
    assert instrument_block()["inspector_sha256"] == ROUND_8_INSPECTOR


def test_first_useful_content_skips_preambles_and_empty_deltas() -> None:
    """Counting an immediate empty event as delivery is the defect this guards.

    A role-only preamble, an empty delta and a whitespace-only delta are all events. None
    of them is useful content, and a gateway that streams nothing usable for most of a
    second should not report a single-digit time-to-first-anything.
    """
    role_only = 'data: {"choices":[{"delta":{"role":"assistant"}}]}'
    empty_delta = 'data: {"choices":[{"delta":{}}]}'
    whitespace = 'data: {"choices":[{"delta":{"content":"   "}}]}'
    done = "data: [DONE]"
    real = 'data: {"choices":[{"delta":{"content":"You sent: x"}}]}'

    assert probe._first_useful(role_only) is False
    assert probe._first_useful(empty_delta) is False
    assert probe._first_useful(whitespace) is False
    assert probe._first_useful(done) is False
    assert probe._first_useful(real) is True


def test_sibling_field_content_counts_as_useful() -> None:
    """The `sse-json-field` carrier puts the value beside `content`, not inside it."""
    sibling = 'data: {"choices":[{"delta":{"content":"","record_field":"abc"}}]}'

    assert probe._first_useful(sibling) is True


def test_whole_response_body_concatenates_deltas_and_folds_siblings() -> None:
    """The carrier mapping, asserted rather than described in a docstring.

    A non-streaming response has ONE object, so a value that streams across two sibling
    fields arrives whole in one. That is deliberate: the arm's job is to be the easiest
    possible condition, so that a failure in it means the feature does not work at all.
    """
    events = [
        {"content": "You sent: "},
        {"content": "hello "},
        {"content": "", "record_field": "sec-"},
        {"content": "", "record_field": "ret"},
    ]

    document = json.loads(probe._whole_response_body(events))
    message = document["choices"][0]["message"]

    assert message["content"] == "You sent: hello "
    assert message["record_field"] == "sec-ret"


def test_the_probe_scores_a_non_streaming_body_with_the_published_inspector() -> None:
    """Arm comparability. `_haystacks` and `_leak_tier` are imported, never reimplemented."""
    body = json.dumps(
        {"choices": [{"message": {"content": "Reference record: probe@example.invalid"}}]}
    )

    assert probe._leak_tier("probe@example.invalid", body) == "literal"
    assert probe._present("probe@example.invalid", probe._haystacks(body)) is True
    assert probe._present("absent@example.invalid", probe._haystacks(body)) is False


def _row(condition: str, *, restored, leaked, ok: bool = True, stable: bool = True) -> dict:
    return {
        "condition": condition,
        "transport": {"ok": ok, "status": 200 if ok else None, "error": None},
        "echo_restored": restored,
        "injection_leaked": leaked,
        "stable_across_repeats": stable,
    }


def test_mode_regression_requires_a_clean_whole_response_arm() -> None:
    """The finding is 'streaming broke it', not 'it never worked'. Those are different."""
    rows = [
        _row("whole-response", restored=True, leaked=False),
        _row("single-chunk", restored=False, leaked=False),
    ]

    verdict = probe.derive_verdict(rows)

    assert verdict["mode_regression"] == "restoration"


def test_a_feature_that_never_worked_is_not_a_mode_regression() -> None:
    """The most likely false positive, and the one that would embarrass the article."""
    rows = [
        _row("whole-response", restored=False, leaked=False),
        _row("single-chunk", restored=False, leaked=False),
    ]

    verdict = probe.derive_verdict(rows)

    assert verdict["mode_regression"] == "none"
    assert any("does not restore in any mode" in r for r in verdict["reasons"])


def test_containment_lost_to_streaming_is_reported_separately() -> None:
    rows = [
        _row("whole-response", restored=True, leaked=False),
        _row("single-chunk", restored=True, leaked=True),
    ]

    assert probe.derive_verdict(rows)["mode_regression"] == "containment"


def test_a_target_that_refuses_non_streaming_is_inconclusive_not_passing() -> None:
    """NeMo answers HTTP 400 when an output rail is configured and streaming is unset.

    The mirror case belongs here: a refused baseline arm must never be read as a clean
    one, because 'it did not answer' and 'it answered correctly' are the two things this
    project's transport accounting exists to keep apart.
    """
    rows = [
        _row("whole-response", restored=None, leaked=None, ok=False),
        _row("single-chunk", restored=True, leaked=True),
    ]

    verdict = probe.derive_verdict(rows)

    assert verdict["mode_regression"] == "inconclusive"
    assert any("inconclusive here, not passing" in r for r in verdict["reasons"])


def test_direct_control_leaks_in_every_condition() -> None:
    """End to end, no gateway: the probe's own passthrough control.

    With nothing in the path every arm must leak and every arm must 'restore', because
    the caller's value was never masked. A probe that cannot reproduce passthrough cannot
    be trusted to report a gateway.
    """
    segments = probe.build_segments("a1b2c3d4e5f60001")
    case = {
        "entity": "EMAIL",
        "encoding": "plain",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
        "fragmentation": "adversarial",
    }
    port = 8813
    url = f"http://127.0.0.1:{port}{probe.UPSTREAM_PATH_HINT}"

    rows = [
        probe.run_condition(
            condition,
            segments,
            case,
            target_url=url,
            model="capture",
            token=None,
            upstream_port=port,
            timeout=20.0,
            repeats=1,
        )
        for condition in probe.CONDITIONS
    ]

    assert [r["transport"]["ok"] for r in rows] == [True, True, True]
    assert [r["injection_leaked"] for r in rows] == [True, True, True]
    assert [r["echo_restored"] for r in rows] == [True, True, True]
    # The arms really did differ in shape: one JSON body, then three and four SSE events.
    assert rows[0]["stream_requested_of_upstream"] is False
    assert rows[1]["stream_requested_of_upstream"] is True
    assert rows[2]["upstream_data_events_written"] > rows[1]["upstream_data_events_written"]
    # And the fragmented arm was reassembled across events, not read from one.
    assert rows[2]["injection_leak_tier"] == "same-path-join"


def test_sse_framing_is_not_mistaken_for_a_redaction_token() -> None:
    """The first control run failed exactly here.

    `data: [DONE]` matches the shape of a mask token (`[UPPERCASE]`), so scanning the raw
    response body reported that every streamed response had acquired a redaction token --
    in a run with no gateway in the path at all. Framing is not content.
    """
    body = (
        'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    assert probe._mask_tokens(probe._client_text(body)) == []


def test_mask_tokens_are_found_in_displayed_content() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"a@<URL> and <CREDIT_CARD>"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    assert probe._mask_tokens(probe._client_text(body)) == ["CREDIT_CARD", "URL"]


def test_mask_tokens_ignore_ordinary_prose_and_json_keys() -> None:
    """Lower case, short runs, and unbracketed words must not look like redactions."""
    assert probe._mask_tokens("see <a> and [ok] and {x} and I/O") == []
    assert probe._mask_tokens("choices delta content finish_reason") == []


def test_client_text_reads_non_streaming_bodies_too() -> None:
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "restored@example.com"}}]}
    )

    assert probe._client_text(body) == "restored@example.com"


def test_client_text_includes_sibling_carrier_fields() -> None:
    """The sse-json-field carrier hides the value beside `content`, not inside it."""
    body = 'data: {"choices":[{"delta":{"content":"","record_field":"<EMAIL_ADDRESS>"}}]}\n\n'

    assert probe._mask_tokens(probe._client_text(body)) == ["EMAIL_ADDRESS"]


def test_an_unstable_arm_cannot_carry_a_verdict() -> None:
    """Greptile P1. `run_condition` keeps the LAST attempt's scores, so when repeats
    disagreed the surviving row was whichever way the final attempt fell. Reading those
    scores without checking stability let a transient attempt publish a definitive
    mode_regression while the same artifact printed the row as UNSTABLE and its own
    limitations said such a row must not be quoted.
    """
    rows = [
        _row("whole-response", restored=True, leaked=False),
        _row("single-chunk", restored=False, leaked=False, stable=False),
    ]

    verdict = probe.derive_verdict(rows)

    assert verdict["mode_regression"] == "inconclusive"
    assert any("cannot carry a verdict" in r for r in verdict["reasons"])


def test_missing_stability_is_treated_as_unconfirmed() -> None:
    """Fail closed: absent is not the same as stable."""
    rows = [
        _row("whole-response", restored=True, leaked=False),
        _row("single-chunk", restored=False, leaked=False),
    ]
    del rows[0]["stable_across_repeats"]

    assert probe.derive_verdict(rows)["mode_regression"] == "inconclusive"


def test_non_positive_repeats_are_rejected_not_clamped() -> None:
    """Greptile P2. `max(1, repeats)` ran one pass while the artifact recorded the number
    the operator asked for, so provenance disagreed with the measurement."""
    segments = probe.build_segments("a1b2c3d4e5f60001")
    case = {
        "entity": "EMAIL",
        "encoding": "plain",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
        "fragmentation": "adversarial",
    }

    with pytest.raises(ValueError, match="at least 1"):
        probe.run_condition(
            "single-chunk",
            segments,
            case,
            target_url="http://127.0.0.1:1/v1/chat/completions",
            model="capture",
            token=None,
            upstream_port=8817,
            timeout=1.0,
            repeats=0,
        )


def test_the_cli_rejects_zero_repeats() -> None:
    with pytest.raises(SystemExit):
        probe.main(["--direct", "--repeats", "0"])
