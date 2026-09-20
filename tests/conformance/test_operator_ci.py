"""CI must detect actual regressions without treating existing leaks as success."""

from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pii_leak_benchmark import ci
from pii_leak_benchmark.http_profile import _build_prompt, extract_fixture
from pii_leak_benchmark.operator_profile import seeded_fixture

ROOT = Path(__file__).resolve().parents[2]


def _run():
    return {"schema": "pii-leak-benchmark/operator-run/v1", "contract": {"seed": "a"},
            "verdict": "CLEAN", "entities": {"EMAIL": "contained"},
            "required_checks": {"sse_validity": True}, "target_version": "a"}


def test_comparison_distinguishes_new_and_existing_failures():
    baseline, current = _run(), _run()
    current["entities"]["EMAIL"] = "leak"
    current["verdict"] = "LEAK"
    assert ci.compare(baseline, current)["regressions"] == ["EMAIL"]
    assert ci.compare(current, current)["status"] == "no regression"
    assert ci.compare(current, baseline)["improvements"] == ["EMAIL"]


@pytest.mark.parametrize("mutation", ["seed", "unmeasured", "missing-entity"])
def test_incompatible_or_incomplete_baseline_fails_closed(mutation):
    baseline, current = _run(), _run()
    if mutation == "seed":
        baseline["contract"]["seed"] = "different"
    elif mutation == "unmeasured":
        baseline["verdict"] = "NOT MEASURED"
    else:
        baseline["entities"] = {}
    with pytest.raises(ValueError):
        ci.compare(baseline, current)


def test_partial_inspection_propagates_to_operator_entities():
    report = {"fixture": {"formats": {"EMAIL": "", "SSN": ""}}, "checks": {
        "configured_upstream_boundary": {"correlated_requests": 1, "uninspectable_requests": 1,
                                         "unattributed_uninspectable_requests": 0,
                                         "leaked_entity_types": ["EMAIL"], "unattributed_leaked_entity_types": []}}}
    assert ci.measured_entities(report) == {"EMAIL": "leak", "SSN": "not measured"}


def test_seeded_workload_round_trips_and_has_no_overlapping_needles():
    generated = set()
    for seed in map(str, range(100)):
        fixture, nonce = seeded_fixture(seed, True)
        assert (fixture, nonce) == seeded_fixture(seed, True)
        assert extract_fixture(_build_prompt(nonce, fixture)) == fixture
        assert not any(a != b and a in b for a in fixture.values() for b in fixture.values())
        generated.add(fixture["EMAIL"])
    assert len(generated) > 90


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_managed_gateways_produce_actionable_regression_and_exit_one(tmp_path):
    current_port, baseline_port, capture_port = _port(), _port(), _port()
    command = f'"{sys.executable}" tests/fixtures/ci_gateway.py --port '
    env = dict(os.environ, PYTHONPATH=str(ROOT / "pii-leak-benchmark"))
    summary = tmp_path / "github-summary.md"
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    output = tmp_path / "reports"
    result = subprocess.run([
        sys.executable, "-m", "pii_leak_benchmark.cli", "ci",
        "--target-base-url", f"http://127.0.0.1:{current_port}/v1",
        "--start-command", command + str(current_port) + " --leak-email",
        "--baseline-base-url", f"http://127.0.0.1:{baseline_port}/v1",
        "--baseline-start-command", command + str(baseline_port),
        "--capture-port", str(capture_port), "--duty", "anonymize", "--iterations", "1",
        "--out", str(output),
    ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "current.json").read_text())
    assert report["verdict"] == "LEAK"
    assert "EMAIL" in report["comparison"]["regressions"]
    assert report["entities"]["SSN"] == "contained"
    assert "Enable or repair request redaction" in summary.read_text()
    # Both managed gateways reach their configured upstream BEFORE they bind their
    # own port, so a capture opened only once measurement began would have killed
    # them during startup instead of measuring them. Their discovery request in the
    # observed paths is the evidence that it was listening the whole time, and that
    # startup traffic is inspected rather than bucketed out of the record.
    for label in ("baseline", "current"):
        raw = json.loads((output / f"{label}.raw.json").read_text())
        boundary = raw["checks"]["configured_upstream_boundary"]
        assert "/v1/models" in boundary["upstream_paths_observed"], label
        assert boundary["uninspectable_requests"] == 0, label
    for port in (current_port, baseline_port):
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.3)


def test_no_regression_does_not_waive_current_leaks(tmp_path, monkeypatch):
    # Exercise main with real floor/candidate HTTP traffic and a previously measured run.
    first = tmp_path / "first"
    args = ["--target-base-url", "capture://self", "--iterations", "1", "--capture-port", str(_port())]
    assert ci.main(args + ["--out", str(first)]) == 1
    assert ci.main(args + ["--out", str(tmp_path / "second"), "--baseline-report", str(first / "current.json")]) == 1
    report = json.loads((tmp_path / "second/current.json").read_text())
    assert report["comparison"]["status"] == "no regression"
    baseline = json.loads((first / "current.json").read_text())
    incompatible = copy.deepcopy(baseline)
    incompatible["contract"]["seed"] = "different"
    path = tmp_path / "incompatible.json"
    path.write_text(json.dumps(incompatible))
    assert ci.main(args + ["--out", str(tmp_path / "third"), "--baseline-report", str(path)]) == 2


def test_a_capture_that_cannot_be_opened_never_starts_the_gateway(tmp_path):
    """Ordering, end to end. The capture is opened first, so when opening it fails
    the managed startup command must not run at all. Starting a gateway and handing
    it an upstream address that nothing answers is the failure this order prevents,
    and a wildcard bind with no advertised URL is the cheapest way to fail closed
    on every platform without depending on who wins a contested port.
    """
    marker = tmp_path / "started"
    command = f"\"{sys.executable}\" -c \"open(r'{marker}', 'w').close()\""
    exit_code = ci.main([
        "--target-base-url", f"http://127.0.0.1:{_port()}/v1",
        "--start-command", command, "--capture-host", "0.0.0.0",
        "--readiness-timeout", "5", "--iterations", "1",
        "--out", str(tmp_path / "unopenable"),
    ])
    assert exit_code == 2
    assert not marker.exists(), "the gateway was started before the capture existed"
    summary = (tmp_path / "unopenable/summary.md").read_text()
    assert "NOT MEASURED" in summary
    assert "LEAK" not in summary


def test_startup_failure_has_a_summary_and_is_not_a_leak(tmp_path):
    assert ci.main(["--target-base-url", f"http://127.0.0.1:{_port()}/v1",
                    "--start-command", f'"{sys.executable}" -c "raise SystemExit(3)"',
                    "--readiness-timeout", "2", "--out", str(tmp_path / "failed")]) == 2
    assert "NOT MEASURED" in (tmp_path / "failed/summary.md").read_text()


def _complete_run(**overrides):
    run = {"schema": "pii-leak-benchmark/operator-run/v1",
           "contract": {"profile": "pii-v1", "duty": "restore", "seed": "a"},
           "verdict": "LEAK", "reason": "An unmasked value reached the capture.",
           "target_version": "1.2.3", "entities": {"EMAIL": "leak"},
           "required_checks": {"sse_validity": True}, "coverage": []}
    run.update(overrides)
    return run


def test_the_summary_carries_the_citation_and_a_way_to_publish_it():
    """Both of the steps that used to sit between a finished run and a published one."""
    run = _complete_run(attestation={"run_url": "https://github.com/o/r/actions/runs/42",
                                     "repository": "o/r"})
    summary = ci.render_summary(run)
    assert "<details><summary><b>Citation block" in summary
    assert "pii-leak-benchmark citation" in summary
    assert "issues/new?" in summary and "conformance-result" in summary
    # The run this result came from, so a reader of the issue can open it.
    assert "actions/runs/42" in summary


def test_the_summary_says_where_the_reports_are_without_naming_the_artifact():
    """`artifact-name` is an input to the action, so this command cannot know it."""
    summary = ci.render_summary(_complete_run())
    assert "uploaded as a build artifact" in summary
    assert "current.raw.json" in summary


def test_rendering_the_summary_reads_no_environment(monkeypatch):
    """It is called directly by tests and by `main`; the run carries its own provenance."""
    for name in ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_SERVER_URL",
                 "GITHUB_ACTIONS", "GITHUB_WORKFLOW_REF"):
        monkeypatch.delenv(name, raising=False)
    summary = ci.render_summary(_complete_run())
    assert "Add this result to the benchmark results wall" in summary
    # No run to point at, and no invented one either.
    assert "actions/runs" not in summary


def test_a_setup_failure_is_not_offered_for_publication():
    """A run with no contract measured nothing, so there is nothing to submit."""
    summary = ci.render_summary({"verdict": "NOT MEASURED", "reason": "Setup failed."})
    assert "Publish this result" not in summary


def test_the_operator_run_records_its_own_provenance(monkeypatch):
    """Without this the citation on `current.json` printed no repository and no run URL."""
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    from pii_leak_benchmark.cite import build_citation
    from pii_leak_benchmark.provenance import build_attestation

    attestation = build_attestation()
    assert attestation["run_url"] == "https://github.com/o/r/actions/runs/42"
    citation = build_citation(_complete_run(attestation=attestation))
    assert "Run URL" in citation and "actions/runs/42" in citation
    # Self-reported, and the block has to keep saying so.
    assert attestation["verification"] == "self-reported"
