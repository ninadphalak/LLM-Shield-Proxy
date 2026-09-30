"""The NeMo recipe measures selected source with a local Presidio output rail."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/nemo-source-reproduction.yml"


def _steps():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow, workflow["jobs"]["reproduce"]["steps"]


def test_nemo_workflow_builds_selected_source_with_pinned_detector():
    workflow, steps = _steps()
    assert "workflow_dispatch" in workflow["on"]
    assert "github.sha" in workflow["jobs"]["reproduce"]["env"]["SOURCE_SELECTOR"]
    checkout = next(step for step in steps if step.get("name") == "Check out selected NeMo source")
    assert checkout["with"]["repository"] == "${{ env.SOURCE_REPOSITORY }}"
    assert checkout["with"]["ref"] == "${{ env.SOURCE_SELECTOR }}"
    assert checkout["with"]["path"] == "proxy-source"
    instrument = next(step for step in steps if step.get("name") == "Check out the pinned response instrument")
    assert instrument["with"]["ref"] == "74d744a72adb053ff4f4025bdd676bba0e5be03d"
    build = next(step for step in steps if step.get("name") == "Build the selected source with pinned local detector dependencies")
    assert "docker build --file - --tag" in build["run"]
    assert '"/source[server,sdd]"' in build["run"]
    assert "presidio-analyzer==2.2.364" in build["run"]
    assert "presidio-anonymizer==2.2.364" in build["run"]
    assert "en_core_web_lg" in build["run"]
    assert "COPY . /source" in build["run"]
    assert "cp -a /source/examples" in build["run"]
    assert "pii-leak-benchmark[validate]==0.2.1" in build["run"]


def test_nemo_configs_match_the_detect_output_rail(tmp_path):
    _, steps = _steps()
    write = next(step for step in steps if step.get("name") == "Write exact output-rail configurations")
    script = write["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    root = tmp_path / "nemo-source-reproduction"
    root.mkdir()
    env = os.environ.copy()
    env.update({"RUNNER_TEMP": str(tmp_path), "GITHUB_ENV": str(tmp_path / "github-env")})
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
    for name, port in (("operator", 8765), ("response", 8799)):
        config = yaml.safe_load((root / name / "config.yml").read_text(encoding="utf-8"))
        assert config["models"][0]["parameters"]["base_url"] == f"http://127.0.0.1:{port}/v1"
        assert config["rails"]["output"]["flows"] == ["detect sensitive data on output"]
        assert config["rails"]["output"]["streaming"]["context_size"] == 50
        assert set(config["rails"]["config"]["sensitive_data_detection"]["output"]["entities"]) == {
            "EMAIL_ADDRESS", "US_SSN", "CREDIT_CARD", "PHONE_NUMBER",
        }


def test_nemo_retains_refused_cases_and_only_uploads_verified_reports():
    _, steps = _steps()
    operator = next(step for step in steps if step.get("id") == "operator")
    assert operator["with"]["artifact-name"] == ""
    assert "@29a9932b085e5b063b8c366c3e6bfca2c4af4b06" in operator["uses"]
    assert operator["with"]["submission-section"] == "false"
    response = next(step for step in steps if step.get("id") == "response")
    assert "--oracle midpoint --seed a1b2c3d4e5f60001" in response["run"]
    assert "--upstream-port 8799 --model config" in response["run"]
    assert "--validate" in response["run"]
    verify = next(step for step in steps if step.get("id") == "verify")
    assert "cases_inconclusive') != 0" not in verify["run"]
    assert "strings(json.loads(content))" in verify["run"]
    assert "b64encode(encoded)" in verify["run"]
    assert "capture_output=True" in verify["run"]
    assert "RESPONSE_PYTHON" in verify["run"]
    assert "not in ('CLEAN', 'LEAK')" in verify["run"]
    upload = next(step for step in steps if step.get("name") == "Upload verified source evidence")
    assert upload["if"] == "always() && steps.verify.outcome == 'success'"
    assert upload["with"]["name"] == "source-reproduction"
    assert ".raw.json" not in upload["with"]["path"]
    assert ".log" not in upload["with"]["path"]
    assert steps[-1]["if"] == "always() && steps.result.outputs.status != 'clean'"
