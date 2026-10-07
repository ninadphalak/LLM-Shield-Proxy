"""Every capability the report CLAIMS must have a test here that demonstrates it.

This file exists because of a specific defect and is the structural answer to it. The v2
report's `inspection_scope` was a paragraph copied from the v1 harness. It described a
recursive walk with decoding that the v2 inspector did not perform, and it stayed wrong
for as long as nobody happened to re-read both the sentence and the code. Six placements
reached the client and scored as no leak while the report said they were inspected.

Being more careful when editing the sentence is not a fix, because the failure mode is
precisely that nobody re-reads it. So the sentence is now generated from
`CLIENT_INSPECTION_CAPABILITIES`, and `test_every_declared_capability_has_a_proof` fails
if any entry in that list has no test below. **A capability cannot be claimed without a
proof, and a claim cannot be widened without the build going red.**

Adding a clause to the report therefore costs a test. That is the intended price.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    CLIENT_INSPECTION_CAPABILITIES,
    CLIENT_INSPECTION_SCOPE,
    _haystacks,
    _present,
)

EMAIL = "nuwpcbba@example.com"
SSN = "219-09-9999"


def _sse(*events: dict) -> str:
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"


def _field(value) -> str:
    return _sse({"choices": [{"delta": {"content": "", "f": value}}]})


def _seen(needle: str, doc: str) -> bool:
    return _present(needle, _haystacks(doc))


# Each entry proves one capability key. The keys must match the registry exactly.
PROOFS = {
    "sse_events": lambda: _seen(EMAIL, _sse({"choices": [{"delta": {"content": EMAIL}}]})),
    "json_parsed": lambda: _seen(EMAIL, _field(EMAIL)),
    "recursive_walk": lambda: (
        _seen(EMAIL, _field({"deep": [{"deeper": EMAIL}]}))
        and _seen(EMAIL, _sse({"choices": [{"delta": {"content": "", EMAIL: "x"}}]}))
        and _seen("2190999999", _field(2190999999))
    ),
    "all_choices": lambda: _seen(
        EMAIL, _sse({"choices": [{"delta": {"content": ""}}, {"delta": {"content": EMAIL}}]})
    ),
    "ordered_content_join": lambda: _seen(
        EMAIL,
        _sse(
            {"choices": [{"delta": {"content": EMAIL[:9]}}]},
            {"choices": [{"delta": {"content": EMAIL[9:]}}]},
        ),
    ),
    "ordered_sibling_join": lambda: _seen(
        EMAIL,
        _sse(
            {"choices": [{"delta": {"content": "", "r": EMAIL[:9]}}]},
            {"choices": [{"delta": {"content": "", "r": EMAIL[9:]}}]},
        ),
    ),
    # A value split with one half in `delta.content` and the other in ANY other field was
    # in no haystack: the per-path channels need the same path, and the legacy
    # non-content join excludes exactly the channel the first half sits in. Both of these
    # were measured as MISSES while the value reached the client whole.
    "ordered_whole_document_join": lambda: (
        _seen(
            EMAIL,
            _sse(
                {"choices": [{"delta": {"content": EMAIL[:9]}}]},
                {"choices": [{"delta": {"raw": {"text": EMAIL[9:]}}}]},
            ),
        )
        and _seen(
            EMAIL,
            _sse(
                {"choices": [{"delta": {"content": EMAIL[:9]}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "",
                                "tool_calls": [{"function": {"arguments": EMAIL[9:]}}],
                            }
                        }
                    ]
                },
            ),
        )
    ),
    # `content` is a string in the chat-completions shape and a list of parts in the
    # multimodal one, and a stream may use both. The part's `type` discriminator splices
    # between the halves in the whole-document join, so that one does not reach it.
    "renderable_subtree_join": lambda: _seen(
        EMAIL,
        _sse(
            {"choices": [{"delta": {"content": EMAIL[:9]}}]},
            {"choices": [{"delta": {"content": [{"type": "text", "text": EMAIL[9:]}]}}]},
        ),
    ),
    "unparseable_events": lambda: _seen(
        EMAIL, "data: not-json but carries " + EMAIL + "\n\ndata: [DONE]\n\n"
    ),
    # `json.loads` keeps the LAST of duplicate names and discards the rest, so a value in
    # a shadowed key entered NEITHER the parsed event nor the parser's residue -- it was
    # scanned nowhere while reaching the client verbatim. RFC 8259 permits duplicate
    # names and does not say which wins, so a client may see either.
    "shadowed_duplicate_keys": lambda: _seen(
        EMAIL,
        'data: {"choices":[{"delta":{"content":"'
        + EMAIL
        + '","content":"[REDACTED]"}}]}\n\ndata: [DONE]\n\n',
    ),
    "base64": lambda: (
        _seen(EMAIL, _field(base64.b64encode(EMAIL.encode()).decode()))
        and _seen(EMAIL, _field(base64.b64encode(base64.b64encode(EMAIL.encode())).decode()))
    ),
    "hex": lambda: _seen(EMAIL, _field(binascii.hexlify(EMAIL.encode()).decode())),
    "percent": lambda: _seen(EMAIL, _field(EMAIL.replace("@", "%40"))),
    "char_code_arrays": lambda: _seen(EMAIL, _field([ord(c) for c in EMAIL])),
    "separators_removed": lambda: _seen(SSN, _field("219 09 9999")),
    "nfkd_confusables": lambda: _seen(SSN, _field("２１９-０９-９９９９")),
}


def test_every_declared_capability_has_a_proof() -> None:
    """The whole point of the file. A clause in the report with no test behind it is the
    defect this guards against, restated."""
    declared = {c.key for c in CLIENT_INSPECTION_CAPABILITIES}
    proved = set(PROOFS)
    assert declared <= proved, (
        "the report claims capabilities nothing here demonstrates: "
        f"{sorted(declared - proved)}. Either add a proof or stop claiming it."
    )
    assert proved <= declared, (
        f"proofs exist for capabilities the report no longer claims: {sorted(proved - declared)}. "
        "Remove the proof or restore the claim; a silent divergence is how this started."
    )


@pytest.mark.parametrize("key", sorted(PROOFS))
def test_capability_holds(key: str) -> None:
    assert PROOFS[key](), (
        f"the report claims {key!r} and the inspector does not do it. A value placed this "
        "way reaches the client and the case scores as 'did not leak'."
    )


def test_scope_string_is_generated_from_the_registry() -> None:
    """If the string is ever hand-written again, this fails."""
    for capability in CLIENT_INSPECTION_CAPABILITIES:
        assert capability.clause in CLIENT_INSPECTION_SCOPE
    assert CLIENT_INSPECTION_SCOPE == "; ".join(
        c.clause for c in CLIENT_INSPECTION_CAPABILITIES
    )


def test_registry_keys_are_unique_and_non_empty() -> None:
    keys = [c.key for c in CLIENT_INSPECTION_CAPABILITIES]
    assert len(keys) == len(set(keys)), "duplicate capability key"
    assert all(k and c.clause for k, c in zip(keys, CLIENT_INSPECTION_CAPABILITIES))


# --------------------------------------------------------------------------------------
# The REQUEST-path scope, same treatment, because it was the worse of the two.
#
# `CLIENT_INSPECTION_SCOPE` was converted to a generated list after a copied sentence was
# caught describing a walk the code did not do. `_boundary_check` was left alone -- and it
# was not merely describing the wrong walk, it performed NO walk at all. It returned a
# literal: `passed: True`, `leaked_entity_types: []`, `uninspectable_requests: 0`, under
# sixty words of recursive-decoding prose, for every run ever emitted.
#
# Demonstrated before it was fixed: a relay that forwarded all four protected values
# verbatim to the capture was certified clean on every one of those fields.
# --------------------------------------------------------------------------------------

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    BOUNDARY_INSPECTION_CAPABILITIES,
    BOUNDARY_INSPECTION_SCOPE,
    RunResult,
    Segments,
    _boundary_check,
)

CARD = "4111-1111-1111-1111"
CASE = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "single_chunk",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


def _segments(echo_email: str = EMAIL) -> Segments:
    return Segments(
        echo={"EMAIL": echo_email, "SSN": SSN, "CARDPAN": CARD},
        injection={"EMAIL": "zzqqwwee@example.com", "SSN": "219-09-1111", "CARDPAN": CARD[:-1] + "9"},
    )


def _result(*bodies: str) -> RunResult:
    return RunResult(
        policy="probe",
        case=CASE,
        client_text="x",
        echo_recovered={},
        echo_observable=True,
        transport_error=None,
        injection_leaked=False,
        events_observed=2,
        upstream_bodies=list(bodies),
        latency_ms=[],
    )


def _boundary(*results: RunResult, segments: Segments | None = None) -> dict:
    return _boundary_check(list(results), segments or _segments())


def _egressed(*bodies_per_case: tuple[str, ...]) -> list[str]:
    """Entity types the boundary check says reached the configured upstream."""
    results = [_result(*bodies) for bodies in bodies_per_case]
    return _boundary(*results)["leaked_entity_types"]


BOUNDARY_PROOFS = {
    # The old code read `results[0]` and nothing else. A value that egressed on any
    # other case was invisible, which is 31 of 32 cases in a real run.
    "boundary_every_request": lambda: _egressed(
        ('{"messages":[{"content":"clean"}]}',),
        ('{"messages":[{"content":"see ' + EMAIL + '"}]}',),
    ) == ["EMAIL"],
    "boundary_json_parsed": lambda: _egressed(
        ('{"messages":[{"role":"user","content":"' + EMAIL + '"}]}',),
    ) == ["EMAIL"],
    "boundary_recursive_walk": lambda: (
        _egressed(('{"a":{"b":[{"c":"' + EMAIL + '"}]}}',)) == ["EMAIL"]
        and _egressed(('{"' + EMAIL + '":"x"}',)) == ["EMAIL"]
        # A digit run survives as a JSON NUMBER, holding no strings at all.
        and _boundary(
            _result('{"n":2190999999}'),
            segments=Segments(echo={"SSN": "2190999999"}, injection={"SSN": "111"}),
        )["leaked_entity_types"] == ["SSN"]
    ),
    # A value split across two captured requests at the same JSON path.
    "boundary_ordered_join": lambda: _egressed(
        ('{"messages":[{"content":"' + EMAIL[:9] + '"}]}',
         '{"messages":[{"content":"' + EMAIL[9:] + '"}]}'),
    ) == ["EMAIL"],
    "boundary_unparseable": lambda: _egressed(
        ("this is not JSON at all, it just carries " + EMAIL,),
    ) == ["EMAIL"],
    "boundary_decoded": lambda: _egressed(
        ('{"blob":"' + base64.b64encode(EMAIL.encode()).decode() + '"}',),
    ) == ["EMAIL"],
    "boundary_normalized": lambda: (
        _boundary(_result('{"s":"219 09 9999"}'))["leaked_entity_types"] == ["SSN"]
        and _boundary(_result('{"s":"２１９-０９-９９９９"}'))["leaked_entity_types"] == ["SSN"]
    ),
    "boundary_correlation": lambda: (
        _boundary(_result('{"messages":[]}'))["correlation_mechanism"] == "in-process-capture"
        and _boundary(_result('{"messages":[]}'))["marker_words_observed_max"] == 0
    ),
}


def test_every_declared_boundary_capability_has_a_proof() -> None:
    declared = {c.key for c in BOUNDARY_INSPECTION_CAPABILITIES}
    proved = set(BOUNDARY_PROOFS)
    assert declared == proved, (
        "request-path scope and its proofs have diverged: "
        f"claimed-but-unproved {sorted(declared - proved)}, "
        f"proved-but-unclaimed {sorted(proved - declared)}"
    )


@pytest.mark.parametrize("key", sorted(BOUNDARY_PROOFS))
def test_boundary_capability_holds(key: str) -> None:
    assert BOUNDARY_PROOFS[key](), (
        f"the report claims {key!r} of the request path and the check does not do it."
    )


def test_boundary_scope_string_is_generated_from_the_registry() -> None:
    assert BOUNDARY_INSPECTION_SCOPE == "; ".join(
        c.clause for c in BOUNDARY_INSPECTION_CAPABILITIES
    )


def test_the_boundary_check_is_not_a_constant() -> None:
    """The regression in one assertion: values that egressed must fail the check.

    A relay forwarding the prompt verbatim put all three protected values in the
    captured body and the report said `passed: true, leaked_entity_types: []`.
    """
    clean = _boundary(_result('{"messages":[{"content":"Please review: [EMAIL_1]"}]}'))
    assert clean["passed"] is True
    assert clean["leaked_entity_types"] == []

    leaked = _boundary(
        _result('{"messages":[{"content":"Please review: ' + EMAIL + ", " + SSN + '"}]}')
    )
    assert leaked["passed"] is False
    assert leaked["leaked_entity_types"] == ["EMAIL", "SSN"]


def test_a_run_that_captured_nothing_cannot_pass_the_boundary_check() -> None:
    """No captured request is no evidence, not clean evidence."""
    assert _boundary(_result())["passed"] is False
