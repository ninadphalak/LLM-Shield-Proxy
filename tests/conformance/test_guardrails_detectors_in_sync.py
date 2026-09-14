"""The Guardrails AI gateway's detectors must be the profile's, character for character.

`benchmarks/guardrails-v2-profile/gateway.py` runs in a separate 3.12 environment, because
software under test does not belong in the harness environment. It therefore cannot import
`pii_leak_benchmark._DETECTORS` and has to carry a copy.

**A copy is only safe while something fails when it drifts.** The whole claim of that row is
that the detector is held CONSTANT while the accumulation strategy varies -- it is compared
directly against `chunk-local` (no retention) and `bounded-retention` (`L = N-1`). If the
copied patterns diverge by one character, the row silently stops being a comparison of
retention strategies and becomes a comparison of detectors, while still being published as
the former.

This reads the gateway as text rather than importing it, since importing it would require
the other environment.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import _DETECTORS  # noqa: E402

GATEWAY = REPO / "benchmarks" / "guardrails-v2-profile" / "gateway.py"


def _gateway_source() -> str:
    return GATEWAY.read_text(encoding="utf-8")


def _flags(call: ast.Call) -> int:
    """The real `re` flag value of a `re.compile(...)` call, by evaluating the flag args.

    This was `len(call.args) > 1`, which is a test of ARITY, not of the flag. Changing
    `re.I` to `re.M` in the gateway made its EMAIL detector case-sensitive and the guard
    stayed green -- demonstrated. A flag expression this cannot evaluate is an error, not
    a zero, because silently reading an unknown flag as "no flags" is the same failure a
    third time.
    """
    import re as _re

    value = 0
    for argument in list(call.args[1:]) + [k.value for k in call.keywords if k.arg == "flags"]:
        value |= int(eval(compile(ast.Expression(argument), "<flags>", "eval"), {"re": _re}))
    return value


def _gateway_detectors() -> list[tuple[str, str, int]]:
    """(entity, pattern, flags) triples from the gateway's DETECTORS literal."""
    tree = ast.parse(_gateway_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "DETECTORS" for t in node.targets):
            continue
        out = []
        for element in node.value.elts:  # type: ignore[attr-defined]
            entity = element.elts[0].value
            call = element.elts[1]  # re.compile(...)
            pattern = call.args[0].value
            out.append((entity, pattern, _flags(call)))
        return out
    raise AssertionError("no DETECTORS assignment found in the gateway")


@pytest.mark.skipif(not GATEWAY.exists(), reason="guardrails profile not present")
def test_the_gateway_carries_the_profiles_detectors_verbatim() -> None:
    theirs = _gateway_detectors()
    # `re.UNICODE` is implicit for str patterns and is not something either side chose,
    # so it is masked out of both. Everything else must match exactly.
    import re as _re

    mask = ~_re.UNICODE.value
    theirs = [(e, p, f & mask) for e, p, f in theirs]
    ours = [
        (entity, pattern.pattern, pattern.flags & mask) for entity, pattern in _DETECTORS
    ]
    assert theirs == ours, (
        "the Guardrails gateway's detectors have drifted from the profile's.\n"
        f"  gateway: {theirs}\n"
        f"  profile: {ours}\n"
        "That row is published as 'one detector, three accumulation strategies'. With "
        "different detectors it is not that comparison, and the numbers must not sit "
        "beside chunk-local and bounded-retention."
    )


@pytest.mark.skipif(not GATEWAY.exists(), reason="guardrails profile not present")
def test_the_gateway_actually_applies_every_detector() -> None:
    """Matching the LITERAL is not enough; the row's claim is about what runs.

    Demonstrated: changing the gateway's redaction loop to `DETECTORS[:1]` -- so SSN,
    CARDPAN and USPHONE are never redacted at all -- left the literal untouched and this
    file green. The row would still have been published as "one detector, three
    accumulation strategies" while measuring one entity out of four.
    """
    tree = ast.parse(_gateway_source())
    uses: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            uses.append(node.iter)

    named = [u for u in uses if isinstance(u, ast.Name) and u.id == "DETECTORS"]
    sliced = [
        u for u in uses
        if isinstance(u, ast.Subscript)
        and isinstance(u.value, ast.Name)
        and u.value.id == "DETECTORS"
    ]
    assert not sliced, (
        "the gateway iterates a SLICE of DETECTORS, so some entities are never scanned "
        "while the copied literal still matches the profile's."
    )
    assert named, (
        "nothing in the gateway iterates DETECTORS. The copied literal agreeing with the "
        "profile says nothing if the detectors are not the ones being run."
    )
