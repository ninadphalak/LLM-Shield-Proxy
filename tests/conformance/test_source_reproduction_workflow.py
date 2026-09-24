"""The copyable source-build workflow keeps its benchmark and evidence contract."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/source-reproduction.yml"


def test_source_reproduction_workflow_uses_selected_source_and_pinned_instrument():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["source_ref"]["default"] == "v1.6.6"
    assert workflow["jobs"]["reproduce"]["env"]["ENABLE_EXT_PROC"] == "false"
    steps = workflow["jobs"]["reproduce"]["steps"]
    checkout = next(step for step in steps if step.get("name") == "Check out selected proxy source")
    assert checkout["with"]["ref"] == "${{ inputs.source_ref || 'v1.6.6' }}"
    install = next(step for step in steps if step.get("name") == "Build and install the selected source")
    assert "pip install ." in install["run"]
    assert "pii-leak-benchmark[validate]==0.2.1" in install["run"]
    operator = next(step for step in steps if step.get("id") == "operator")
    assert operator["with"]["upstream-env"] == "UPSTREAM_BASE_URL"
    assert operator["with"]["start-command"].startswith("python -m uvicorn ")
    staged = next(step for step in steps if step.get("name") == "Stage operator reports with the response pair")
    assert "current.json current.raw.json summary.md" in staged["run"]


def test_source_reproduction_workflow_keeps_both_response_arms_and_safe_outputs():
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["reproduce"]["steps"]
    response = next(step for step in steps if step.get("id") == "response")
    assert "run_arm true source-response-on" in response["run"]
    assert "run_arm false source-response-off" in response["run"]
    assert "--oracle midpoint --seed a1b2c3d4e5f60001" in response["run"]
    assert "--upstream-port 8799 --model capture" in response["run"]
    assert "--validate" in response["run"]
    assert "trap cleanup EXIT" in response["run"]
    assert '"$RUNNER_TEMP/$label.log"' in response["run"]
    assert "benchmarks/results/" not in text
    assert "secrets." not in text
    artifact = next(step for step in steps if step.get("name") == "Upload source and response evidence")
    assert artifact["if"] == "always()"
    assert artifact["with"]["name"] == "source-reproduction"
    assert artifact["with"]["path"] == "${{ runner.temp }}/source-reproduction/"
