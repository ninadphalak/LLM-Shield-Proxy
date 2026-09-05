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


def _gateway_detectors() -> list[tuple[str, str, bool]]:
    """(entity, pattern, ignorecase) triples from the gateway's DETECTORS literal."""
    tree = ast.parse(GATEWAY.read_text(encoding="utf-8"))
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
            ignorecase = len(call.args) > 1
            out.append((entity, pattern, ignorecase))
        return out
    raise AssertionError("no DETECTORS assignment found in the gateway")


@pytest.mark.skipif(not GATEWAY.exists(), reason="guardrails profile not present")
def test_the_gateway_carries_the_profiles_detectors_verbatim() -> None:
    theirs = _gateway_detectors()
    ours = [
        (entity, pattern.pattern, bool(pattern.flags & 2))  # re.IGNORECASE == 2
        for entity, pattern in _DETECTORS
    ]
    assert theirs == ours, (
        "the Guardrails gateway's detectors have drifted from the profile's.\n"
        f"  gateway: {theirs}\n"
        f"  profile: {ours}\n"
        "That row is published as 'one detector, three accumulation strategies'. With "
        "different detectors it is not that comparison, and the numbers must not sit "
        "beside chunk-local and bounded-retention."
    )
