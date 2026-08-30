from __future__ import annotations

import json
from pathlib import Path

from llm_shield_proxy.cli import benchmark_main
from llm_shield_proxy.conformance import run_conformance, write_conformance_report


def test_conformance_checks_fragmentation_sse_and_upstream_boundary(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    monkeypatch.setenv("LLM_SHIELD_SOURCE_REVISION", "test-revision")

    report = run_conformance(iterations=20)

    assert report["passed"] is True
    assert report["source_revision"] == "test-revision"
    assert report["schema"].endswith("/v1.0.0")
    assert set(report["checks"]) == {
        "fragmentation_safety",
        "raw_pii_egress",
        "sse_validity",
        "rehydration_fidelity",
        "audit_integrity",
        "latency_measurement",
        "memory_bounded",
    }
    assert report["checks"]["fragmentation_safety"]["passed"] is True
    assert report["checks"]["fragmentation_safety"]["partitions_tested"] >= 2
    assert report["checks"]["sse_validity"]["done_markers"] == 1
    assert report["checks"]["sse_validity"]["utf8_mid_codepoint_split_preserved"] is True
    assert report["checks"]["rehydration_fidelity"]["expected_value_reconstructed"] is True
    assert report["checks"]["raw_pii_egress"]["leaked_entity_types"] == []
    assert report["checks"]["raw_pii_egress"]["payload_content_included"] is False
    assert report["checks"]["audit_integrity"]["tamper_negative_control_detected"] is True
    assert report["checks"]["latency_measurement"]["threshold_enforced"] is False
    assert report["checks"]["memory_bounded"]["rss_threshold_enforced"] is False
    assert "excludes ASGI" in report["microbenchmarks"]["scope"]


def test_conformance_output_contains_no_test_pii(tmp_path):
    report = run_conformance(iterations=10)
    path = write_conformance_report(report, str(tmp_path / "result.json"))
    content = Path(path).read_text(encoding="utf-8")

    assert "person@example.invalid" not in content
    assert "123-45-6789" not in content
    assert "4532-1234-5678-9012" not in content


def test_benchmark_cli_writes_machine_readable_report(tmp_path, capsys):
    destination = tmp_path / "conformance.json"

    exit_code = benchmark_main(["--iterations", "10", "--json-out", str(destination)])

    assert exit_code == 0
    assert json.loads(destination.read_text(encoding="utf-8"))["passed"] is True
    assert "local in-process" in capsys.readouterr().out


def test_conformance_rejects_too_few_iterations():
    try:
        run_conformance(iterations=1)
    except ValueError as exc:
        assert "at least 10" in str(exc)
    else:
        raise AssertionError("Expected low iteration count to be rejected")
