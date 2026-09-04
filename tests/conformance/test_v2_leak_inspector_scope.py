"""The v2 leak inspector must see everything its report claims it sees.

Every emitted report asserts an `inspection_scope` of "every SSE event the client
received: event data parsed as JSON and walked recursively over all types ... with
base64/hex/percent-encoded runs and character-code arrays decoded".

That sentence was copied from the v1 harness while the v2 inspector walked only
`choices[0].delta` and only its string-valued members. The claim was v1's; the code was
not. Six placements reached the client and scored as no leak, and the worst of them,
`tool_calls[].function.arguments`, is a standard OpenAI response field carrying
model-generated text: a gateway that redacted `delta.content` and nothing else scored a
perfect LeakRate of 0.00.

Every case here is a FALSE PASS if it regresses -- the direction a leak instrument must
never fail in.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import _haystacks, _present  # noqa: E402

NEEDLE = "nuwpcbba@example.com"


def _sse(*events: dict) -> str:
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"


PLACEMENTS = {
    "delta_content": {"choices": [{"delta": {"content": NEEDLE}}]},
    "sibling_string": {"choices": [{"delta": {"content": "", "x": NEEDLE}}]},
    "nested_object": {"choices": [{"delta": {"content": "", "meta": {"note": NEEDLE}}}]},
    "list_member": {"choices": [{"delta": {"content": "", "items": [NEEDLE]}}]},
    "deeply_nested": {"choices": [{"delta": {"content": "", "a": {"b": [{"c": NEEDLE}]}}}]},
    "second_choice": {"choices": [{"delta": {"content": ""}}, {"delta": {"content": NEEDLE}}]},
    "top_level_field": {"choices": [{"delta": {"content": ""}}], "note": NEEDLE},
    "tool_call_arguments": {
        "choices": [{"delta": {"content": "", "tool_calls": [{"function": {"arguments": NEEDLE}}]}}]
    },
    "object_key": {"choices": [{"delta": {"content": "", NEEDLE: "x"}}]},
}


@pytest.mark.parametrize("name", sorted(PLACEMENTS))
def test_a_value_anywhere_in_the_event_is_seen(name: str) -> None:
    assert _present(NEEDLE, _haystacks(_sse(PLACEMENTS[name]))), (
        f"a value placed at {name} reached the client and the inspector did not see it; "
        "the case would score as 'did not leak'"
    )


def test_base64_encoded_value_is_decoded() -> None:
    encoded = base64.b64encode(NEEDLE.encode()).decode()
    assert _present(NEEDLE, _haystacks(_sse({"choices": [{"delta": {"content": encoded}}]})))


def test_character_code_array_is_reconstructed() -> None:
    codes = [ord(c) for c in NEEDLE]
    assert _present(NEEDLE, _haystacks(_sse({"choices": [{"delta": {"content": "", "c": codes}}]})))


def test_non_json_event_is_still_scanned() -> None:
    """A frame this code cannot parse still reached the client."""
    raw = "data: not-json-but-contains " + NEEDLE + "\n\ndata: [DONE]\n\n"
    assert _present(NEEDLE, _haystacks(raw))


def test_split_across_events_still_reassembles() -> None:
    """The ordered content stream is what makes fragmentation measurable. Widening the
    walk must not break it: nothing may be spliced between two halves of a value."""
    left, right = NEEDLE[:9], NEEDLE[9:]
    sse = _sse(
        {"choices": [{"delta": {"content": left}}]},
        {"choices": [{"delta": {"content": right}}]},
    )
    assert _present(NEEDLE, _haystacks(sse))


def test_split_halves_are_not_reassembled_across_unrelated_text() -> None:
    """Guard against the opposite error. If unrelated event text were spliced into the
    ordered stream, two halves separated by other content would still join and the
    inspector would report leaks that did not happen."""
    sse = _sse(
        {"choices": [{"delta": {"content": NEEDLE[:9]}}], "junk": "----------"},
        {"choices": [{"delta": {"content": NEEDLE[9:]}}]},
    )
    assert _present(NEEDLE, _haystacks(sse)), "genuine adjacent-fragment reassembly broke"

    apart = _sse(
        {"choices": [{"delta": {"content": NEEDLE[:9]}}]},
        {"choices": [{"delta": {"content": "UNRELATED"}}]},
        {"choices": [{"delta": {"content": NEEDLE[9:]}}]},
    )
    assert not _present(NEEDLE, _haystacks(apart)), (
        "halves separated by other CONTENT were joined; that is a false leak report"
    )


def test_absent_value_is_not_reported() -> None:
    """The instrument must be able to say no."""
    clean = _sse({"choices": [{"delta": {"content": "nothing sensitive here"}}]})
    assert not _present(NEEDLE, _haystacks(clean))


def test_split_across_two_events_SIBLING_fields_reassembles() -> None:
    """The case the first version of this file did not cover, and it regressed.

    Sibling fields need their own ORDERED stream, exactly like delta content. Widening
    the walk to use the recursive collector alone joined object keys and decoded material
    between the two halves and the join stopped reassembling them.

    A unit test did not catch it. The KNOWN-ANSWER CONTROL did: `passthrough` forwards
    everything, so it must leak 1.00 in both conditions, and it reported 0.75 adversarial.
    That is the argument for keeping a policy in the suite whose answer is known in
    advance.
    """
    left, right = NEEDLE[:9], NEEDLE[9:]
    sse = _sse(
        {"choices": [{"delta": {"content": "", "record_field": left}}]},
        {"choices": [{"delta": {"content": "", "record_field": right}}]},
    )
    assert _present(NEEDLE, _haystacks(sse)), (
        "a value split across two events' sibling fields was not reassembled"
    )


def test_passthrough_style_stream_leaks_everything_it_carries() -> None:
    """A known-answer control at the inspector level: if every needle placement is
    present in the events, every one must be found. An inspector that misses any of them
    would let a forward-everything gateway score better than it deserves."""
    events = [{"choices": [{"delta": {"content": "", "f": value}}]} for value in
              (NEEDLE, "219-09-9999", "4111111111111111", "212-555-0143")]
    hay = _haystacks(_sse(*events))
    for needle in (NEEDLE, "219-09-9999", "4111111111111111", "212-555-0143"):
        assert _present(needle, hay), f"{needle} was carried to the client and not seen"
