from __future__ import annotations

import json
import os
from pathlib import Path

from llm_shield_proxy.cli import benchmark_main
from llm_shield_proxy.conformance import run_conformance, write_conformance_report


def test_conformance_checks_fragmentation_sse_and_upstream_boundary(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    # GITHUB_SHA wins over the override by design, so on a GitHub runner this asserted
    # the runner's commit and failed. It has been red in CI since 2026-08-30 while
    # passing on every developer machine, which is the whole failure mode of a test
    # that reads the ambient environment.
    monkeypatch.delenv("GITHUB_SHA", raising=False)
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
    assert report["checks"]["memory_bounded"]["rss_threshold_enforced"] is False
    # Deleted on purpose: it gated on percentiles of monotonic-clock deltas being
    # non-negative, so it could not fail. Timings are still published, as
    # measurements rather than as a passed gate.
    assert "latency_measurement" not in report["checks"]
    assert report["microbenchmarks"]["no_op"]
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


def test_local_profile_runs_on_a_fresh_install_without_an_encryption_key(tmp_path, monkeypatch):
    """`llm-shield-proxy benchmark` must work with nothing configured.

    The stream-digest receipt HMACs with SHIELD_ENCRYPTION_KEY and fails closed when it is
    unset. That is correct for a serving process and it made this command exit 2 on every
    fresh install from 2026-08-30 onwards -- including in this repository's own public
    benchmark workflow, which was red for that reason while the documented reproduction
    steps told readers to run exactly this. Reproduced in a subprocess, because the parent
    pytest process has a key from the developer's .env.
    """
    import subprocess
    import sys

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("SHIELD_ENCRYPTION_KEY",)
    }
    environment["TELEMETRY_ENABLED"] = "false"
    environment.pop("TELEMETRY_ENDPOINT_URL", None)
    destination = tmp_path / "local.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "llm_shield_proxy.cli", "benchmark",
            "--iterations", "20", "--json-out", str(destination),
        ],
        capture_output=True, text=True, env=environment, timeout=600,
        # Away from the repository root on purpose: Settings reads a local `.env`, and the
        # maintainer's own .env supplies the key that hid this defect for two days.
        cwd=str(tmp_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ephemeral evaluation-only key" in result.stderr
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["passed"] is True
