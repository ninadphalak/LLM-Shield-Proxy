"""Offline, privacy-safe pilot assessment for representative LLM traffic."""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable, Optional

from llm_shield_proxy.compliance.oscal import build_assessment_results, build_observation, iso_timestamp
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

_ASSESSMENT_UUID_NAMESPACE = uuid.UUID("2e62eb5c-0541-44ec-92d0-d9f1458e09de")


def _package_version() -> str:
    try:
        return version("llm-shield-proxy")
    except PackageNotFoundError:
        return "source"


def _generated_at() -> datetime:
    """Honor SOURCE_DATE_EPOCH so assessments can be reproduced exactly."""
    source_epoch = os.getenv("SOURCE_DATE_EPOCH")
    if source_epoch is not None:
        return datetime.fromtimestamp(int(source_epoch), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_assessment_records(path: Path) -> list[dict[str, Any]]:
    """Load a JSON object/array or JSONL file without retaining it in reports."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
        candidates: Iterable[Any] = payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        candidates = [json.loads(line) for line in text.splitlines() if line.strip()]

    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        if isinstance(candidate, str):
            records.append({"input": candidate})
        elif isinstance(candidate, dict):
            records.append(candidate)
        else:
            raise ValueError(f"Assessment record {index} must be a JSON object or string")
    return records


def _json_size(record: dict[str, Any]) -> int:
    return len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def run_assessment(
    input_path: str,
    *,
    enable_tier2: Optional[bool] = None,
    enable_tier3: Optional[bool] = None,
    assessment_plan_href: Optional[str] = None,
) -> dict[str, Any]:
    """Assess records locally and return aggregate metadata only.

    No transformed records, source text, matched values, or redaction tokens are
    returned. Each record receives a fresh in-memory vault which is discarded
    immediately after its aggregate counters have been collected.
    """
    source = Path(input_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Assessment input not found: {input_path}")

    tier2 = settings.ENABLE_TIER2_ENTROPY if enable_tier2 is None else enable_tier2
    tier3 = settings.ENABLE_TIER3_ONNX_NER if enable_tier3 is None else enable_tier3
    engine = PIIEngine(enable_tier2=tier2, enable_tier3=tier3)
    records = load_assessment_records(source)
    entity_counts: Counter[str] = Counter()
    records_with_findings = 0
    total_bytes = 0

    for record in records:
        total_bytes += _json_size(record)
        vault = Vault(synthetic=False)
        engine.redact_payload(record, vault)
        if vault.type_counters:
            records_with_findings += 1
            entity_counts.update(vault.type_counters)

        # Explicitly release references to raw values as soon as counters are read.
        vault.original_to_token.clear()
        vault.token_to_original.clear()

    generated = _generated_at()
    source_sha256 = _sha256_path(source)
    identity = ":".join(
        [source_sha256, str(tier2), str(tier3), str(settings.SHANNON_ENTROPY_THRESHOLD)]
    )
    findings_total = sum(entity_counts.values())
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "generated_at": iso_timestamp(generated),
        "generator": {"name": "llm-shield-proxy", "version": _package_version()},
        "source": {
            "sha256": source_sha256,
            "records": len(records),
            "bytes": total_bytes,
            "content_included": False,
        },
        "configuration": {
            "tier1_regex": True,
            "tier2_entropy": tier2,
            "tier3_onnx_ner": tier3,
            "entropy_threshold": settings.SHANNON_ENTROPY_THRESHOLD,
        },
        "findings": {
            "total": findings_total,
            "records_with_findings": records_with_findings,
            "records_without_findings": len(records) - records_with_findings,
            "entity_counts": dict(sorted(entity_counts.items())),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": sys.implementation.name,
        },
        "privacy": {
            "raw_records_persisted": False,
            "redacted_records_persisted": False,
            "network_calls_performed": False,
        },
    }

    observations = [
        build_observation(
            title=f"Detected {entity_type}",
            description="Aggregate protected-data detection; no matched value is included.",
            properties={"entity_type": entity_type, "count": count},
            observation_uuid=str(
                uuid.uuid5(_ASSESSMENT_UUID_NAMESPACE, f"{identity}:observation:{entity_type}")
            ),
        )
        for entity_type, count in sorted(entity_counts.items())
    ]
    report["oscal"] = build_assessment_results(
        title="LLM-Shield-Proxy Offline Privacy Assessment",
        description="Offline assessment of representative LLM traffic without upstream transmission.",
        observations=observations,
        assessment_plan_href=assessment_plan_href or "urn:uuid:76db2a7a-7c27-4b31-b4c4-ef477d266f21",
        generated_at=generated,
        result_title="Offline Protected Data Assessment",
        document_uuid=str(uuid.uuid5(_ASSESSMENT_UUID_NAMESPACE, f"{identity}:document")),
        result_uuid=str(uuid.uuid5(_ASSESSMENT_UUID_NAMESPACE, f"{identity}:result")),
    )
    return report


def render_assessment_html(report: dict[str, Any]) -> str:
    """Render a dependency-free aggregate HTML report."""
    findings = report["findings"]
    rows = "".join(
        f"<tr><td>{html.escape(entity)}</td><td>{count}</td></tr>"
        for entity, count in findings["entity_counts"].items()
    ) or '<tr><td colspan="2">No findings</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LLM-Shield Assessment</title>
<style>body{{font:16px system-ui;max-width:900px;margin:40px auto;padding:0 20px}}table{{border-collapse:collapse}}td,th{{border:1px solid #bbb;padding:8px 12px}}code{{word-break:break-all}}</style></head>
<body><h1>LLM-Shield-Proxy Offline Assessment</h1>
<p>Generated: {html.escape(report['generated_at'])}</p>
<p>Input fingerprint: <code>{html.escape(report['source']['sha256'])}</code></p>
<ul><li>Records: {report['source']['records']}</li><li>Records with findings: {findings['records_with_findings']}</li><li>Total findings: {findings['total']}</li></ul>
<h2>Findings by entity type</h2><table><thead><tr><th>Entity</th><th>Count</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Privacy statement</h2><p>This report contains aggregate metadata only. Source and transformed records are not included, and assessment performs no network calls.</p>
</body></html>"""


def write_assessment_report(report: dict[str, Any], output_directory: str) -> dict[str, str]:
    """Write JSON, HTML, and OSCAL artifacts to an output directory."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)

    public_report = dict(report)
    oscal = public_report.pop("oscal")
    paths = {
        "json": output / "assessment.json",
        "html": output / "assessment.html",
        "oscal": output / "oscal-assessment-results.json",
    }
    paths["json"].write_text(json.dumps(public_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths["html"].write_text(render_assessment_html(public_report), encoding="utf-8")
    paths["oscal"].write_text(json.dumps(oscal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {name: str(path) for name, path in paths.items()}
