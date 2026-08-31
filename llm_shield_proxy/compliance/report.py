"""Compliance-Pack Bundler.

Aggregates NIST OSCAL assessment artifacts, hash-chained/Ed25519-signed audit
log evidence, and a SHA-256 file-integrity manifest into a single
auditor-deliverable .zip archive, alongside a generated Markdown summary.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_FRAMEWORK_NARRATIVES: Dict[str, Dict[str, str]] = {
    "hipaa": {
        "title": "HIPAA Security Rule Compliance Pack",
        "reference": "45 CFR Part 164, Subpart C (Security Rule)",
        "narrative": (
            "This pack evidences technical safeguards under the HIPAA Security Rule: PII/PHI "
            "redaction at the point of egress (45 CFR §164.312(a)/(e)) and an audit-control "
            "mechanism (45 CFR §164.312(b)) implemented as a cryptographically hash-chained "
            "and Ed25519-signed audit trail. Immutable WORM retention, when required, "
            "is configured in the operator's evidence store."
        ),
    },
    "soc2": {
        "title": "SOC 2 Trust Services Criteria Evidence Support Pack",
        "reference": "AICPA Trust Services Criteria – Security & Confidentiality (CC6, CC7)",
        "narrative": (
            "This pack evidences logical access and system-monitoring controls under SOC 2 Trust "
            "Services Criteria CC6 (Logical Access) and CC7 (System Operations): configured "
            "redaction observations plus signed, hash-linked records for instrumented proxy paths. "
            "Completeness and control effectiveness require independent assessment."
        ),
    },
    "nist": {
        "title": "NIST SP 800-53 Rev. 5 Evidence Support Pack",
        "reference": "NIST SP 800-53 Rev. 5 (AU-9, AU-10, PE-19)",
        "narrative": (
            "This pack bundles machine-readable NIST OSCAL assessment-results artifacts (control "
            "family AU: Audit and Accountability; PE-19: Information Leakage) generated from the "
            "proxy's runtime governance decisions, chained to signed tamper-evident audit records."
        ),
    },
}

SUPPORTED_FRAMEWORKS = tuple(sorted(_FRAMEWORK_NARRATIVES))


def _reconstruct_canonical_bytes(record: Dict[str, Any], exclude: tuple) -> bytes:
    """Reconstruct the exact byte string hashed and signed for an audit record.

    llm_shield_proxy.observability.audit appends "hash", then "signature" and
    "public_key_fingerprint", to an already-sorted JSON string rather than re-serializing
    the whole record. Verification must therefore preserve on-disk key order (as produced
    by json.loads, which is insertion order) instead of re-sorting keys.
    """
    stripped = {k: v for k, v in record.items() if k not in exclude}
    return json.dumps(stripped).encode("utf-8")


def verify_worm_log(audit_log_path: Optional[str], pubkey_pem: Optional[str] = None) -> Dict[str, Any]:
    """Parse a hash-chained audit JSONL file, verifying SHA-256 continuity and,
    when a public key is supplied, Ed25519 receipt signatures."""
    summary: Dict[str, Any] = {
        "source_path": audit_log_path,
        "total_events": 0,
        "chain_valid": True,
        "chain_breaks": [],
        "signature_checked": False,
        "signatures_valid": 0,
        "signatures_invalid": 0,
        "unsigned_events": 0,
        "event_counts": {},
        "severity_counts": {},
        "first_timestamp": None,
        "last_timestamp": None,
        "fingerprints_seen": [],
        "chain_ids_seen": [],
        "first_sequence": None,
        "last_sequence": None,
        "terminal_hash": None,
    }

    if not audit_log_path:
        summary["chain_valid"] = None
        return summary

    path = Path(audit_log_path)
    if not path.exists():
        summary["chain_valid"] = None
        summary["error"] = f"Audit log not found: {audit_log_path}"
        return summary

    verifier_key = None
    if pubkey_pem:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        verifier_key = load_pem_public_key(pubkey_pem.encode("utf-8"))
        summary["signature_checked"] = True

    fingerprints_seen: set = set()
    chain_ids_seen: set = set()
    prior_hash: Optional[str] = None
    prior_chain_id: Optional[str] = None
    prior_sequence: Optional[int] = None
    expected_fingerprint: Optional[str] = None
    if verifier_key is not None:
        from cryptography.hazmat.primitives import serialization

        public_raw = verifier_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        expected_fingerprint = hashlib.sha256(public_raw).hexdigest()

    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                summary["chain_valid"] = False
                summary["chain_breaks"].append({"line": line_no, "reason": "invalid_json"})
                continue

            summary["total_events"] += 1
            event_name = record.get("event", "UNKNOWN")
            severity = record.get("severity", "INFO")
            summary["event_counts"][event_name] = summary["event_counts"].get(event_name, 0) + 1
            summary["severity_counts"][severity] = summary["severity_counts"].get(severity, 0) + 1

            timestamp = record.get("timestamp")
            if timestamp:
                if summary["first_timestamp"] is None:
                    summary["first_timestamp"] = timestamp
                summary["last_timestamp"] = timestamp

            record_hash = record.get("hash")
            if record_hash:
                expected_hash = hashlib.sha256(
                    _reconstruct_canonical_bytes(record, exclude=("hash", "signature", "public_key_fingerprint"))
                ).hexdigest()
                if expected_hash != record_hash:
                    summary["chain_valid"] = False
                    summary["chain_breaks"].append({"line": line_no, "reason": "hash_mismatch", "event": event_name})
            else:
                summary["chain_valid"] = False
                summary["chain_breaks"].append({"line": line_no, "reason": "missing_hash", "event": event_name})

            chain_id = record.get("chain_id")
            if chain_id:
                chain_ids_seen.add(str(chain_id))
            same_chain = prior_chain_id is None or chain_id is None or chain_id == prior_chain_id
            if same_chain and prior_hash is not None and record.get("previous_hash") != prior_hash:
                summary["chain_valid"] = False
                summary["chain_breaks"].append(
                    {"line": line_no, "reason": "chain_discontinuity", "event": event_name}
                )
            sequence = record.get("sequence")
            if sequence is not None:
                try:
                    sequence_int = int(sequence)
                    if summary["first_sequence"] is None:
                        summary["first_sequence"] = sequence_int
                    summary["last_sequence"] = sequence_int
                    if same_chain and prior_sequence is not None and sequence_int != prior_sequence + 1:
                        summary["chain_valid"] = False
                        summary["chain_breaks"].append(
                            {"line": line_no, "reason": "sequence_discontinuity", "event": event_name}
                        )
                    prior_sequence = sequence_int
                except (TypeError, ValueError):
                    summary["chain_valid"] = False
                    summary["chain_breaks"].append(
                        {"line": line_no, "reason": "invalid_sequence", "event": event_name}
                    )
            prior_hash = record_hash or prior_hash
            prior_chain_id = str(chain_id) if chain_id else prior_chain_id

            fingerprint = record.get("public_key_fingerprint")
            signature = record.get("signature")
            if fingerprint:
                fingerprints_seen.add(fingerprint)
                if expected_fingerprint is not None and fingerprint != expected_fingerprint:
                    summary["chain_valid"] = False
                    summary["chain_breaks"].append(
                        {"line": line_no, "reason": "public_key_fingerprint_mismatch", "event": event_name}
                    )

            if signature:
                if verifier_key is not None:
                    from cryptography.exceptions import InvalidSignature

                    canonical = _reconstruct_canonical_bytes(record, exclude=("signature", "public_key_fingerprint"))
                    try:
                        verifier_key.verify(base64.b64decode(signature), canonical)
                        summary["signatures_valid"] += 1
                    except (InvalidSignature, ValueError, TypeError, binascii.Error):
                        summary["signatures_invalid"] += 1
                        summary["chain_valid"] = False
                        summary["chain_breaks"].append(
                            {"line": line_no, "reason": "invalid_signature", "event": event_name}
                        )
            else:
                summary["unsigned_events"] += 1

    summary["fingerprints_seen"] = sorted(fingerprints_seen)
    summary["chain_ids_seen"] = sorted(chain_ids_seen)
    summary["terminal_hash"] = prior_hash
    return summary


def summarize_oscal(oscal_file_path: Optional[str]) -> Dict[str, Any]:
    """Loads a persisted OSCAL assessment-results artifact (JSON or JSONL), or generates a
    fresh, empty one when no persisted artifact is available."""
    summary: Dict[str, Any] = {
        "source_path": oscal_file_path,
        "result_count": 0,
        "observation_count": 0,
        "raw_artifact": None,
    }

    if not oscal_file_path:
        from llm_shield_proxy.compliance.trace_exporter import DecisionTraceExporter

        artifact_bytes = DecisionTraceExporter().generate_oscal_artifact()
        summary["raw_artifact"] = artifact_bytes
        payload = json.loads(artifact_bytes)
        results = payload.get("assessment-results", {}).get("results", [])
        summary["result_count"] = len(results)
        summary["observation_count"] = sum(len(r.get("observations", [])) for r in results)
        return summary

    path = Path(oscal_file_path)
    if not path.exists():
        summary["error"] = f"OSCAL artifact not found: {oscal_file_path}"
        summary["raw_artifact"] = b"{}"
        return summary

    text = path.read_text(encoding="utf-8").strip()
    records: List[Dict[str, Any]] = []
    try:
        records = [json.loads(text)]
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))

    for rec in records:
        results = rec.get("assessment-results", {}).get("results", [])
        summary["result_count"] += len(results)
        summary["observation_count"] += sum(len(r.get("observations", [])) for r in results)

    summary["raw_artifact"] = text.encode("utf-8") if text else b"{}"
    return summary


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_markdown_summary(
    framework: str,
    audit_summary: Dict[str, Any],
    oscal_summary: Dict[str, Any],
    checksums: Dict[str, str],
    generated_at: str,
) -> str:
    meta = _FRAMEWORK_NARRATIVES[framework]
    chain_status = audit_summary["chain_valid"]
    chain_label = (
        "N/A (no audit log provided)"
        if chain_status is None
        else ("VALID" if chain_status else "TAMPER / DISCONTINUITY DETECTED")
    )

    lines = [
        f"# {meta['title']}",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Framework reference:** {meta['reference']}  ",
        "**Generator:** `llm-shield-proxy compliance-report`",
        "",
        "## Summary",
        "",
        meta["narrative"],
        "",
        "## Hash-Chained Audit Evidence",
        "",
        f"- Source: `{audit_summary.get('source_path') or 'not provided'}`",
        f"- Total events: {audit_summary['total_events']}",
        f"- Hash-chain integrity: **{chain_label}**",
        (
            f"- Ed25519 signature verification: "
            f"{'performed' if audit_summary['signature_checked'] else 'not performed (no --pubkey-file supplied)'}"
        ),
    ]
    if audit_summary["signature_checked"]:
        lines.append(f"  - Valid signatures: {audit_summary['signatures_valid']}")
        lines.append(f"  - Invalid signatures: {audit_summary['signatures_invalid']}")
    lines.append(f"- Unsigned events: {audit_summary['unsigned_events']}")
    if audit_summary["chain_breaks"]:
        lines.append(
            f"- ⚠ {len(audit_summary['chain_breaks'])} integrity issue(s) detected "
            "— see `audit_chain_breaks.json`"
        )
    if audit_summary.get("first_timestamp"):
        lines.append(f"- Window: {audit_summary['first_timestamp']} → {audit_summary['last_timestamp']}")

    if audit_summary["event_counts"]:
        lines.append("")
        lines.append("| Event Type | Count |")
        lines.append("| :--- | ---: |")
        for event, count in sorted(audit_summary["event_counts"].items()):
            lines.append(f"| {event} | {count} |")

    oscal_source_label = oscal_summary.get("source_path") or "generated fresh (no persisted OSCAL artifact provided)"
    lines += [
        "",
        "## NIST OSCAL Assessment Results",
        "",
        f"- Source: `{oscal_source_label}`",
        f"- Assessment result sets: {oscal_summary['result_count']}",
        f"- Observations: {oscal_summary['observation_count']}",
        "",
        "## File Integrity Manifest (SHA-256)",
        "",
        "| File | SHA-256 |",
        "| :--- | :--- |",
    ]
    for fname, digest in sorted(checksums.items()):
        lines.append(f"| {fname} | `{digest}` |")

    lines += [
        "",
        "---",
        "*This pack was generated automatically from proxy-side artifacts. Chain of custody is*",
        "*anchored by the SHA-256 hash chain and, where signed, the Ed25519 receipts documented above.*",
    ]
    return "\n".join(lines) + "\n"


def generate_compliance_pack(
    framework: str,
    out_path: str,
    audit_log_path: Optional[str] = None,
    oscal_file_path: Optional[str] = None,
    pubkey_file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds an auditor-deliverable .zip compliance pack for the given framework."""
    if framework not in _FRAMEWORK_NARRATIVES:
        raise ValueError(f"Unsupported framework '{framework}'. Choose one of {SUPPORTED_FRAMEWORKS}.")

    pubkey_pem = Path(pubkey_file_path).read_text(encoding="utf-8") if pubkey_file_path else None

    audit_summary = verify_worm_log(audit_log_path, pubkey_pem)
    oscal_summary = summarize_oscal(oscal_file_path)

    generated_at = datetime.now(timezone.utc).isoformat()
    oscal_bytes = oscal_summary.pop("raw_artifact", None) or b"{}"

    audit_chain_breaks_bytes = json.dumps(audit_summary["chain_breaks"], indent=2, sort_keys=True).encode("utf-8")
    audit_summary_bytes = json.dumps(audit_summary, indent=2, sort_keys=True, default=str).encode("utf-8")

    checksums: Dict[str, str] = {
        "oscal_assessment_results.json": hashlib.sha256(oscal_bytes).hexdigest(),
        "audit_summary.json": hashlib.sha256(audit_summary_bytes).hexdigest(),
        "audit_chain_breaks.json": hashlib.sha256(audit_chain_breaks_bytes).hexdigest(),
    }
    audit_log_file = Path(audit_log_path) if audit_log_path else None
    if audit_log_file and audit_log_file.exists():
        checksums["source_audit_log.jsonl"] = _sha256_file(audit_log_file)

    markdown = _render_markdown_summary(framework, audit_summary, oscal_summary, checksums, generated_at)

    out = Path(out_path)
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SUMMARY.md", markdown)
        zf.writestr("oscal_assessment_results.json", oscal_bytes)
        zf.writestr("audit_summary.json", audit_summary_bytes)
        zf.writestr("audit_chain_breaks.json", audit_chain_breaks_bytes)
        zf.writestr("checksums.sha256.json", json.dumps(checksums, indent=2, sort_keys=True))
        if audit_log_file and audit_log_file.exists():
            zf.write(audit_log_file, arcname="source_audit_log.jsonl")

    return {
        "out_path": str(out),
        "audit_summary": audit_summary,
        "oscal_summary": oscal_summary,
        "checksums": checksums,
    }
