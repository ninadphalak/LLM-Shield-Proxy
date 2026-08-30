"""Small OSCAL Assessment Results builders shared by reports and exporters.

The helpers deliberately emit metadata-only evidence. They never include prompt
text, detected values, or reversible redaction tokens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

DEFAULT_ASSESSMENT_PLAN_HREF = "urn:uuid:76db2a7a-7c27-4b31-b4c4-ef477d266f21"


def iso_timestamp(value: Optional[datetime] = None) -> str:
    """Return an RFC 3339 UTC timestamp accepted by OSCAL date-time fields."""
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_uuid() -> str:
    """Return an OSCAL-compatible UUID string."""
    return str(uuid.uuid4())


def build_observation(
    *,
    title: str,
    description: str,
    properties: Optional[Mapping[str, Any]] = None,
    method: str = "TEST",
    observation_uuid: Optional[str] = None,
) -> dict[str, Any]:
    """Build a privacy-safe OSCAL observation."""
    evidence: dict[str, Any] = {"description": "LLM-Shield-Proxy generated evidence"}
    if properties:
        evidence["props"] = [
            {"name": str(name), "value": str(value)} for name, value in sorted(properties.items())
        ]

    return {
        "uuid": observation_uuid or new_uuid(),
        "title": title,
        "description": description,
        "methods": [method],
        "relevant-evidence": [evidence],
    }


def build_assessment_results(
    *,
    title: str,
    description: str,
    observations: Iterable[Mapping[str, Any]],
    assessment_plan_href: str = DEFAULT_ASSESSMENT_PLAN_HREF,
    generated_at: Optional[datetime] = None,
    result_title: str = "LLM-Shield-Proxy Automated Assessment",
    document_uuid: Optional[str] = None,
    result_uuid: Optional[str] = None,
) -> dict[str, Any]:
    """Build an OSCAL 1.2 Assessment Results document.

    ``assessment_plan_href`` must identify the assessment plan governing the
    assessment. The default is an explicit placeholder URN and must be replaced
    with the deployment's actual plan before an artifact is used as formal audit
    evidence.
    """
    timestamp = iso_timestamp(generated_at)
    return {
        "assessment-results": {
            "uuid": document_uuid or new_uuid(),
            "metadata": {
                "title": title,
                "last-modified": timestamp,
                "version": "1.0.0",
                "oscal-version": "1.2.0",
            },
            "import-ap": {"href": assessment_plan_href},
            "results": [
                {
                    "uuid": result_uuid or new_uuid(),
                    "title": result_title,
                    "description": description,
                    "start": timestamp,
                    "observations": [dict(observation) for observation in observations],
                }
            ],
        }
    }
