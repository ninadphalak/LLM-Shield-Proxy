"""The LiteLLM recipe tests selected source against pinned local dependencies."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/litellm-source-reproduction.yml"


def _steps():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow, workflow["jobs"]["reproduce"]["steps"]


def test_litellm_workflow_is_copyable_source_recipe_with_pinned_instruments():
    workflow, steps = _steps()
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["jobs"]["reproduce"]["runs-on"] == "ubuntu-latest"
    checkout = next(step for step in steps if step.get("name") == "Check out selected LiteLLM source")
    assert checkout["with"]["repository"] == "${{ env.SOURCE_REPOSITORY }}"
    assert checkout["with"]["ref"] == "${{ env.SOURCE_SELECTOR }}"
    assert checkout["with"]["path"] == "proxy-source"
    instrument = next(step for step in steps if step.get("name") == "Check out the pinned response instrument")
    assert instrument["with"]["ref"] == "74d744a72adb053ff4f4025bdd676bba0e5be03d"
    build = next(step for step in steps if step.get("name") == "Build source image over pinned runtime dependencies")
    assert "docker build --file - --tag" in build["run"]
    assert "COPY litellm/" in build["run"]
    assert "sha256:570a872d2fde8f1bc4a147634810941103c14697270f0f2918c5aa7d8201cac5" in build["run"]
    assert "pii-leak-benchmark[validate]==0.2.1" in build["run"]


def test_litellm_configs_route_both_profiles_to_local_capture(tmp_path):
    _, steps = _steps()
    write = next(step for step in steps if step.get("name") == "Write exact operator and response configurations")
    script = write["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    root = tmp_path / "litellm-source-reproduction"
    root.mkdir()
    env = os.environ.copy()
    env.update({"RUNNER_TEMP": str(tmp_path), "GITHUB_ENV": str(tmp_path / "github-env"),
                "ANALYZER_PORT": "30001", "ANONYMIZER_PORT": "30002"})
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
    for name, port in (("operator", 8765), ("response", 8799)):
        config = yaml.safe_load((root / f"{name}-config.yaml").read_text(encoding="utf-8"))
        assert config["model_list"][0]["litellm_params"]["api_base"] == f"http://127.0.0.1:{port}/v1"
        guardrail = config["guardrails"][0]["litellm_params"]
        assert guardrail["presidio_analyzer_api_base"] == "http://127.0.0.1:30001"
        assert guardrail["presidio_anonymizer_api_base"] == "http://127.0.0.1:30002"
        assert guardrail["output_parse_pii"] is True


def test_litellm_measures_and_uploads_only_verified_value_free_reports():
    _, steps = _steps()
    operator = next(step for step in steps if step.get("id") == "operator")
    assert operator["with"]["artifact-name"] == ""
    assert "@73a433906f4f7a2d071a44c780485b8ce3cca541" in operator["uses"]
    response = next(step for step in steps if step.get("id") == "response")
    assert "--oracle midpoint --seed a1b2c3d4e5f60001" in response["run"]
    assert "--upstream-port 8799 --model capture" in response["run"]
    assert "--validate" in response["run"]
    assert "benchmark-instrument" == response["working-directory"]
    verify = next(step for step in steps if step.get("id") == "verify")
    assert "strings(json.loads(content))" in verify["run"]
    assert "capture_output=True" in verify["run"]
    assert "RESPONSE_PYTHON" in verify["run"]
    upload = next(step for step in steps if step.get("name") == "Upload verified source evidence")
    assert upload["if"] == "always() && steps.verify.outcome == 'success'"
    assert upload["with"]["name"] == "source-reproduction"
    assert "if-no-files-found" in upload["with"]
    assert ".log" not in upload["with"]["path"]
    assert ".raw.json" not in upload["with"]["path"]
    assert "steps.operator.outcome == 'failure'" in steps[-1]["if"]
