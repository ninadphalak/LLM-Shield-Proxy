"""Compliance-Pack Bundler Test Suite.

Validates WORM audit hash-chain verification, Ed25519 receipt-signature
verification, OSCAL summarization, and end-to-end .zip pack generation via
the `llm-shield-proxy compliance-report` CLI.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from llm_shield_proxy.cli import compliance_report_main
from llm_shield_proxy.compliance.report import (
    SUPPORTED_FRAMEWORKS,
    generate_compliance_pack,
    summarize_oscal,
    verify_worm_log,
)


def _write_signed_worm_log(tmp_path, tamper: bool = False, invalidate_signature: bool = False):
    """Builds a small, self-consistent WORM-chained + Ed25519-signed audit log JSONL file
    replicating the exact construction used by llm_shield_proxy.observability.audit."""
    import base64
    import hashlib

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pubkey_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    fingerprint = hashlib.sha256(
        public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    ).hexdigest()

    lines = []
    previous_hash = "0" * 68
    for i in range(3):
        base_dict = {
            "timestamp": f"2026-01-0{i + 1}T00:00:00+00:00",
            "event": "PII_REDACTION_EVENT",
            "severity": "INFO",
            "instance_id": "test-instance",
            "previous_hash": previous_hash,
        }
        event_str = json.dumps(base_dict, sort_keys=True)
        new_hash = hashlib.sha256(event_str.encode("utf-8")).hexdigest()
        final_str = f'{event_str[:-1]}, "hash": "{new_hash}"}}'

        signature_b64 = base64.b64encode(private_key.sign(final_str.encode("utf-8"))).decode("ascii")
        if invalidate_signature and i == 1:
            signature_b64 = base64.b64encode(b"not-a-real-signature-0000000000000000000000000000000000").decode(
                "ascii"
            )
        final_str = f'{final_str[:-1]}, "signature": "{signature_b64}", "public_key_fingerprint": "{fingerprint}"}}'

        record = json.loads(final_str)
        if tamper and i == 2:
            record["event"] = "TAMPERED_EVENT"

        lines.append(json.dumps(record))
        previous_hash = new_hash

    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pubkey_path = tmp_path / "pubkey.pem"
    pubkey_path.write_text(pubkey_pem, encoding="utf-8")

    return log_path, pubkey_path


def test_verify_worm_log_missing_path_returns_none_status():
    summary = verify_worm_log(None)
    assert summary["chain_valid"] is None
    assert summary["total_events"] == 0


def test_verify_worm_log_nonexistent_file(tmp_path):
    summary = verify_worm_log(str(tmp_path / "does-not-exist.jsonl"))
    assert summary["chain_valid"] is None
    assert "error" in summary


def test_verify_worm_log_valid_chain_and_signatures(tmp_path):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path)
    pubkey_pem = pubkey_path.read_text(encoding="utf-8")

    summary = verify_worm_log(str(log_path), pubkey_pem)

    assert summary["total_events"] == 3
    assert summary["chain_valid"] is True
    assert summary["chain_breaks"] == []
    assert summary["signature_checked"] is True
    assert summary["signatures_valid"] == 3
    assert summary["signatures_invalid"] == 0
    assert summary["event_counts"] == {"PII_REDACTION_EVENT": 3}


def test_verify_worm_log_detects_tampered_record(tmp_path):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path, tamper=True)
    pubkey_pem = pubkey_path.read_text(encoding="utf-8")

    summary = verify_worm_log(str(log_path), pubkey_pem)

    assert summary["chain_valid"] is False
    assert len(summary["chain_breaks"]) >= 1


def test_verify_worm_log_detects_invalid_signature(tmp_path):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path, invalidate_signature=True)
    pubkey_pem = pubkey_path.read_text(encoding="utf-8")

    summary = verify_worm_log(str(log_path), pubkey_pem)

    assert summary["signatures_invalid"] == 1
    assert summary["chain_valid"] is False


def test_verify_worm_log_without_pubkey_skips_signature_check(tmp_path):
    log_path, _ = _write_signed_worm_log(tmp_path)
    summary = verify_worm_log(str(log_path), pubkey_pem=None)

    assert summary["chain_valid"] is True
    assert summary["signature_checked"] is False
    assert summary["signatures_valid"] == 0


def test_verify_worm_log_detects_sequence_gap(tmp_path):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path)
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    for index, record in enumerate(records, start=1):
        record["chain_id"] = "test-chain"
        record["sequence"] = index if index < 3 else 4
    # Adding fields invalidates hashes too, but the verifier must independently
    # report the sequence discontinuity.
    log_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    summary = verify_worm_log(str(log_path), pubkey_path.read_text(encoding="utf-8"))

    assert summary["chain_valid"] is False
    assert any(item["reason"] == "sequence_discontinuity" for item in summary["chain_breaks"])


def test_summarize_oscal_generates_fresh_shell_when_no_file_given():
    summary = summarize_oscal(None)
    assert summary["result_count"] >= 1
    assert summary["observation_count"] == 0


def test_summarize_oscal_missing_file(tmp_path):
    summary = summarize_oscal(str(tmp_path / "missing.json"))
    assert "error" in summary


def test_generate_compliance_pack_rejects_unsupported_framework(tmp_path):
    with pytest.raises(ValueError):
        generate_compliance_pack(framework="pci-dss", out_path=str(tmp_path / "out.zip"))


@pytest.mark.parametrize("framework", SUPPORTED_FRAMEWORKS)
def test_generate_compliance_pack_end_to_end(tmp_path, framework):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path)
    out_path = tmp_path / "pack.zip"

    result = generate_compliance_pack(
        framework=framework,
        out_path=str(out_path),
        audit_log_path=str(log_path),
        pubkey_file_path=str(pubkey_path),
    )

    assert out_path.exists()
    assert result["audit_summary"]["chain_valid"] is True

    with zipfile.ZipFile(out_path) as zf:
        names = set(zf.namelist())
        assert {
            "SUMMARY.md",
            "oscal_assessment_results.json",
            "audit_summary.json",
            "audit_chain_breaks.json",
            "checksums.sha256.json",
            "source_audit_log.jsonl",
        }.issubset(names)

        checksums = json.loads(zf.read("checksums.sha256.json"))
        assert set(checksums) == {
            "oscal_assessment_results.json",
            "audit_summary.json",
            "audit_chain_breaks.json",
            "source_audit_log.jsonl",
        }

        summary_md = zf.read("SUMMARY.md").decode("utf-8")
        assert "VALID" in summary_md


def test_compliance_report_cli_writes_pack(tmp_path, capsys):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path)
    out_path = tmp_path / "cli_pack.zip"

    exit_code = compliance_report_main(
        [
            "--framework",
            "soc2",
            "--out",
            str(out_path),
            "--audit-log",
            str(log_path),
            "--pubkey-file",
            str(pubkey_path),
        ]
    )

    assert exit_code == 0
    assert out_path.exists()
    captured = capsys.readouterr()
    assert "Compliance pack written to" in captured.out
    assert "SOC2" in captured.out


def test_compliance_report_cli_nonzero_exit_on_tamper(tmp_path):
    log_path, pubkey_path = _write_signed_worm_log(tmp_path, tamper=True)
    out_path = tmp_path / "cli_pack_tampered.zip"

    exit_code = compliance_report_main(
        [
            "--framework",
            "nist",
            "--out",
            str(out_path),
            "--audit-log",
            str(log_path),
            "--pubkey-file",
            str(pubkey_path),
        ]
    )

    assert exit_code == 1
