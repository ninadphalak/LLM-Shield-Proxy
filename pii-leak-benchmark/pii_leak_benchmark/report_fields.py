"""Read a fact out of a report without caring which shape wrote it.

TWO SHAPES, ONE SET OF FACTS. This package emits research-profile reports (v2, FIDE)
and operator runs (``pii-leak-benchmark/operator-run/v1``), and they file the same
information under different names:

===================  ==============================  ==========================
fact                 research profile                operator run
===================  ==============================  ==========================
harness              ``harness_revision``            ``contract.harness_version``
target name          ``implementation.name``         not recorded
target version       ``implementation.version``      ``target_version``
scorer digest        ``instrument.inspector_sha256`` ``contract.instrument_sha256``
===================  ==============================  ==========================

WHY IT IS A MODULE. ``badge`` and ``cite`` both need these, and they were written
separately: ``badge`` was taught the operator spelling and ``cite`` was not, so running
``cite`` on a CI report printed ``unrecorded`` for the harness, target and version while
the report beside it held every one of them. The page tells submitters to cite exactly
that file. One resolver, imported by both, is what stops the two drifting again.

TARGET NAME IS NOT INFERRED. An operator run genuinely does not record which gateway was
measured; it records the model alias it routed through, which is a different fact. This
returns "unrecorded" rather than substituting the alias, because a wrong name asserts
something the run never measured while a blank merely says so.

Standard library only, and nothing here may import ``llm_shield_proxy``.
"""

from __future__ import annotations

from typing import Any, Optional

MISSING = "unrecorded"


def dig(report: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def harness_revision(report: dict[str, Any]) -> str:
    """Operator spelling first: it is the shape the CI action produces."""
    return str(
        dig(report, "contract", "harness_version")
        or report.get("harness_revision")
        or MISSING
    )


def target_name(report: dict[str, Any]) -> str:
    """The gateway measured, or MISSING. Never the model alias -- see the module note."""
    return str(dig(report, "implementation", "name") or MISSING)


def target_version(report: dict[str, Any]) -> str:
    return str(
        report.get("target_version")
        or dig(report, "implementation", "version")
        or MISSING
    )


def model(report: dict[str, Any]) -> Optional[str]:
    """The model or routing alias the run went through, when recorded."""
    value = dig(report, "contract", "model")
    return str(value) if value else None


def instrument_sha256(report: dict[str, Any]) -> Optional[str]:
    """The scorer digest, which is how a reader tells a stock instrument from a doctored
    one. Both shapes record it, in different places."""
    value = dig(report, "contract", "instrument_sha256") or dig(
        report, "instrument", "inspector_sha256"
    )
    return str(value) if value else None
