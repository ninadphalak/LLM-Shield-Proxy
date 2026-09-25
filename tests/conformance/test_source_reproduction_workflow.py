"""The copyable source-build workflow keeps its benchmark and evidence contract."""

import ast
import json
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/source-reproduction.yml"


def test_source_reproduction_workflow_uses_selected_source_and_pinned_instrument():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["source_ref"]["default"] == "v1.6.6"
    assert workflow["jobs"]["reproduce"]["env"]["ENABLE_EXT_PROC"] == "false"
    steps = workflow["jobs"]["reproduce"]["steps"]
    checkout = next(step for step in steps if step.get("name") == "Check out selected proxy source")
    assert checkout["with"]["ref"] == "${{ inputs.source_ref || github.sha }}"
    install = next(step for step in steps if step.get("name") == "Build and install the selected source")
    assert "pip install ." in install["run"]
    assert "pii-leak-benchmark[validate]==0.2.1" in install["run"]
    operator = next(step for step in steps if step.get("id") == "operator")
    assert operator["with"]["upstream-env"] == "UPSTREAM_BASE_URL"
    assert "@73a433906f4f7a2d071a44c780485b8ce3cca541" in operator["uses"]
    assert "source" not in operator["with"]
    assert operator["with"]["artifact-name"] == ""
    assert operator["with"]["start-command"].startswith("python -m uvicorn ")
    staged = next(step for step in steps if step.get("name") == "Stage operator reports with the response pair")
    assert "current.json summary.md" in staged["run"]
    assert "current.raw.json" not in staged["run"]
    record = next(step for step in steps if step.get("name") == "Record source and environment")
    assert "set -euo pipefail" in record["run"]


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
    assert '"$RUNNER_TEMP/$label-instrument.log"' in response["run"]
    assert "benchmarks/results/" not in text
    assert "secrets." not in text
    artifact = next(step for step in steps if step.get("name") == "Upload source and response evidence")
    assert artifact["if"] == "always() && steps.verify.outcome == 'success'"
    assert artifact["with"]["name"] == "source-reproduction"
    paths = artifact["with"]["path"].splitlines()
    assert len(paths) == 10
    assert any(path.endswith("/operator-packages.txt") for path in paths)
    assert any(path.endswith("/source-response-on.json") for path in paths)
    assert any(path.endswith("/source-response-off.json") for path in paths)
    assert any(path.endswith("/verification.txt") for path in paths)
    assert not any(path.endswith(".raw.json") or path.endswith(".log") for path in paths)
    verify = next(step for step in steps if step.get("id") == "verify")
    assert verify["env"]["BENCHMARK_PYTHON"] == "${{ steps.operator.outputs.python-path }}"
    assert 'build_segments("a1b2c3d4e5f60001")' in verify["run"]
    assert "RESPONSE_PYTHON" in verify["run"]
    assert "capture_output=True" in verify["run"]
    assert "source-commit.txt" in verify["run"]
    assert "seeded_fixture(contract['seed']" in verify["run"]
    assert "not in ('CLEAN', 'LEAK')" in verify["run"]
    assert "for name in names:" in verify["run"]
    assert "strings(json.loads(content))" in verify["run"]
    assert "quote(value, safe='')" in verify["run"]
    assert "b64encode(encoded)" in verify["run"]
    assert "a required report or provenance file is absent" in verify["run"]
    assert "steps.verify.outcome == 'failure'" in steps[-1]["if"]


def test_source_verifier_scans_decoded_json_strings():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    verify = next(step for step in workflow["jobs"]["reproduce"]["steps"] if step.get("id") == "verify")
    script = verify["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    function = next(node for node in ast.parse(script).body if isinstance(node, ast.FunctionDef) and node.name == "strings")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(WORKFLOW), "exec"), namespace)
    value = 'Zoë "fixture'
    serialized = json.dumps({"nested": [value]})
    assert value not in serialized
    assert any(value in field for field in namespace["strings"](json.loads(serialized)))
