"""Extract common fields from both research-profile and operator-run reports.

Unifies field extraction across shapes to prevent divergence between `badge` and `cite`.
Target names are strictly read, never inferred from model aliases.
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
    """Resolve harness revision (checking operator spelling first)."""
    return str(
        dig(report, "contract", "harness_version")
        or report.get("harness_revision")
        or MISSING
    )


def target_name(report: dict[str, Any]) -> str:
    """The gateway measured, or MISSING. Never falls back to the model alias."""
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
    """The scorer digest, which identifies the exact instrument used."""
    value = dig(report, "contract", "instrument_sha256") or dig(
        report, "instrument", "inspector_sha256"
    )
    return str(value) if value else None
