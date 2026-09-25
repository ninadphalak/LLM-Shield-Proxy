"""The Portkey workflow builds source and retains one response profile."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/portkey-source-reproduction.yml"


def test_portkey_workflow_checks_out_and_builds_the_selected_source():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["pull_request"]["paths"] == [
        ".github/workflows/portkey-source-reproduction.yml"
    ]
    env = workflow["jobs"]["reproduce"]["env"]
    assert "github.repository" in env["SOURCE_REPOSITORY"]
    assert "github.sha" in env["SOURCE_SELECTOR"]
    steps = workflow["jobs"]["reproduce"]["steps"]
    checkout = next(step for step in steps if step.get("name") == "Check out the proxy source")
    assert checkout["with"]["repository"] == "${{ env.SOURCE_REPOSITORY }}"
    assert checkout["with"]["ref"] == "${{ env.SOURCE_SELECTOR }}"
    build = next(step for step in steps if step.get("name") == "Build the checked-out proxy")
    assert "docker build --file Dockerfile" in build["run"]
    record = next(step for step in steps if step.get("name") == "Record source and configuration")
    assert "source-identity.json" in record["run"]
    assert "guardrail-config.json" in record["run"]
    assert "image-id.txt" in record["run"]
    instrument = next(step for step in steps if step.get("name") == "Check out the pinned response instrument")
    assert instrument["with"]["ref"] == "74d744a72adb053ff4f4025bdd676bba0e5be03d"
    assert instrument["with"]["path"] == "benchmark-instrument"


def test_portkey_workflow_preserves_both_profiles_without_report_values():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["reproduce"]["steps"]
    operator = next(step for step in steps if step.get("id") == "operator")
    assert "--network host" in operator["with"]["start-command"]
    assert operator["with"]["duty"] == "anonymize"
    assert operator["with"]["source"] == "pii-leak-benchmark==0.4.1"
    assert operator["with"]["artifact-name"] == ""
    assert "x-portkey-custom-host=http://127.0.0.1:8765/v1" in operator["env"]["CONFORMANCE_TARGET_HEADERS"]
    response = next(step for step in steps if step.get("id") == "response")
    assert response["working-directory"] == "benchmark-instrument"
    assert "--gateway-url http://127.0.0.1:8787/v1/chat/completions" in response["run"]
    assert "--upstream-port 8799 --model capture" in response["run"]
    assert "--oracle midpoint --seed a1b2c3d4e5f60001" in response["run"]
    assert "'x-portkey-custom-host': 'http://127.0.0.1:8799/v1'" in response["run"]
    assert '"$RUNNER_TEMP/portkey-instrument.log"' in response["run"]
    stage = next(step for step in steps if step.get("name") == "Stage the operator result")
    assert "current.json summary.md" in stage["run"]
    assert "current.raw.json" not in stage["run"]
    artifact = next(step for step in steps if step.get("name") == "Upload source-build evidence")
    assert artifact["with"]["name"] == "source-reproduction"
    assert artifact["if"] == "always() && steps.verify.outcome == 'success'"
    paths = artifact["with"]["path"].splitlines()
    assert any(path.endswith("/portkey-source.json") for path in paths)
    assert not any(path.endswith(".raw.json") or path.endswith(".log") for path in paths)
    assert any(path.endswith("/verification.txt") for path in paths)
    verify = next(step for step in steps if step.get("id") == "verify")
    assert "build_segments('a1b2c3d4e5f60001')" in verify["run"]
    assert "seeded_fixture(contract['seed']" in verify["run"]
    assert "for name in names:" in verify["run"]
    assert "get('self_probe')" in verify["run"]
    assert "captured_requests') != 32" not in verify["run"]
    assert "benchmarks/results/" not in WORKFLOW.read_text(encoding="utf-8")
