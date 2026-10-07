"""The library-wrapper recipe measures the LLM Guard and Guardrails AI rows from a fork.

One job per wrapper, each at the published library pin, the published harness version and
the published seed, through the same two profiles the proxy recipes run.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wrappers-source-reproduction.yml"
WRAPPERS = ("llm-guard-chunk-local", "llm-guard-buffered", "guardrails-ai")
PUBLISHED = {
    "llm-guard-chunk-local": ("llm-guard==0.3.16", "benchmarks/llm-guard-v2-profile/gateway.py"),
    "llm-guard-buffered": ("llm-guard==0.3.16", "benchmarks/llm-guard-v2-profile/gateway.py"),
    "guardrails-ai": ("guardrails-ai==0.10.2", "benchmarks/guardrails-v2-profile/gateway.py"),
}


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _steps(job):
    return _workflow()["jobs"][job]["steps"]


def _select(wrapper):
    step = next(step for step in _steps("select") if step.get("id") == "matrix")
    # The workflow appends the script's stdout to GITHUB_OUTPUT; here stdout is read directly.
    script = step["run"].split("<<'PY' >> \"$GITHUB_OUTPUT\"\n", 1)[1].rsplit("PY\n", 1)[0]
    finished = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, WRAPPER=wrapper),
        capture_output=True, text=True, check=False,
    )
    values = dict(line.split("=", 1) for line in finished.stdout.splitlines() if "=" in line)
    return finished.returncode, values


def test_the_wrapper_input_names_each_wrapper_and_all():
    workflow = _workflow()
    wrapper = workflow["on"]["workflow_dispatch"]["inputs"]["wrapper"]
    assert wrapper["options"] == [*WRAPPERS, "all"]
    assert wrapper["default"] == "all"
    assert workflow["on"]["pull_request"]["paths"] == [
        ".github/workflows/wrappers-source-reproduction.yml"
    ]
    assert workflow["permissions"] == {"contents": "read"}


def test_the_select_job_turns_the_input_into_one_job_per_wrapper():
    code, values = _select("all")
    assert code == 0
    matrix = json.loads(values["matrix"])
    assert [entry["wrapper"] for entry in matrix["include"]] == list(WRAPPERS)
    assert values["single"] == "false"
    for entry in matrix["include"]:
        package, gateway = PUBLISHED[entry["wrapper"]]
        assert entry["package"] == package
        assert entry["gateway"] == gateway
        assert (ROOT / gateway).is_file()
        for key in ("product", "configuration", "license", "project_url", "policy", "port", "duty",
                    "request_path_redaction", "mode"):
            assert key in entry, (entry["wrapper"], key)
    policies = {entry["wrapper"]: entry["policy"] for entry in matrix["include"]}
    # The report names are the published row names, so a run compares directly.
    assert policies == {
        "llm-guard-chunk-local": "llm-guard-chunk-local",
        "llm-guard-buffered": "llm-guard-buffered",
        "guardrails-ai": "guardrails-ai-stream-validate",
    }
    for policy in policies.values():
        assert (ROOT / "benchmarks/results/v2-response-split" / f"{policy}.json").is_file()
    modes = {entry["wrapper"]: entry["mode"] for entry in matrix["include"]}
    assert modes == {"llm-guard-chunk-local": "chunk-local", "llm-guard-buffered": "buffered", "guardrails-ai": ""}
    duties = {entry["wrapper"]: entry["duty"] for entry in matrix["include"]}
    assert duties == {"llm-guard-chunk-local": "restore", "llm-guard-buffered": "restore", "guardrails-ai": "anonymize"}
    redaction = {entry["wrapper"]: entry["request_path_redaction"] for entry in matrix["include"]}
    assert redaction == {"llm-guard-chunk-local": "configured", "llm-guard-buffered": "configured",
                         "guardrails-ai": "not-configured"}


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_a_single_wrapper_is_one_job_with_the_intake_artifact_name(wrapper):
    code, values = _select(wrapper)
    assert code == 0
    matrix = json.loads(values["matrix"])
    assert [entry["wrapper"] for entry in matrix["include"]] == [wrapper]
    assert values["single"] == "true"


def test_an_unknown_wrapper_stops_the_run():
    code, values = _select("presidio")
    assert code != 0
    assert "matrix" not in values


def test_each_wrapper_job_runs_both_profiles_at_the_published_pins():
    text = WORKFLOW.read_text(encoding="utf-8")
    job = _workflow()["jobs"]["reproduce"]
    assert job["needs"] == "select"
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.select.outputs.matrix) }}"
    assert job["strategy"]["fail-fast"] == "false"
    env = job["env"]
    for key in ("WALL_PRODUCT", "WALL_CONFIGURATION", "WALL_LICENSE", "WALL_PROJECT_URL"):
        assert env[key].startswith("${{ matrix.")
    assert env["LLMGUARD_MODE"] == "${{ matrix.mode }}"
    assert env["V2_REQUEST_PATH_REDACTION"] == "${{ matrix.request_path_redaction }}"
    assert "source-reproduction" in env["ARTIFACT_NAME"]
    assert "needs.select.outputs.single == 'true'" in env["ARTIFACT_NAME"]
    assert "needs.select.outputs.single != 'true'" in env["SUBMISSION_HOLD_REASON"]

    steps = job["steps"]
    build = next(step for step in steps if step.get("id") == "build")
    assert '"$WRAPPER_PACKAGE"' in build["run"]
    assert "venv-wrapper" in build["run"]
    record = next(step for step in steps if step.get("id") == "source")
    assert "pii-leak-benchmark[validate]==0.2.1" in record["run"]
    assert "source-identity.json" in record["run"]
    assert "'source_selector': os.environ['WRAPPER_PACKAGE']" in record["run"]
    instrument = next(step for step in steps if step.get("name") == "Check out the pinned response instrument")
    assert instrument["with"]["ref"] == "74d744a72adb053ff4f4025bdd676bba0e5be03d"
    assert instrument["with"]["path"] == "benchmark-instrument"

    startup = next(step for step in steps if step.get("id") == "startup")
    assert "8765/v1/chat/completions" in startup["run"]
    operator = next(step for step in steps if step.get("id") == "operator")
    assert "@29a9932b085e5b063b8c366c3e6bfca2c4af4b06" in operator["uses"]
    assert operator["with"]["target-base-url"] == "http://127.0.0.1:${{ matrix.port }}/v1"
    assert "start-command" not in operator["with"]
    assert operator["with"]["duty"] == "${{ matrix.duty }}"
    assert operator["with"]["target-model"] == "capture"
    assert operator["with"]["submission-section"] == "false"
    assert operator["with"]["artifact-name"] == ""
    assert "source" not in operator["with"]
    stage = next(step for step in steps if step.get("name") == "Stage the operator result")
    assert "current.json summary.md" in stage["run"]
    assert "current.raw.json" not in stage["run"]

    response = next(step for step in steps if step.get("id") == "response")
    assert response["working-directory"] == "benchmark-instrument"
    assert "8799/v1/chat/completions" in response["run"]
    assert "--oracle midpoint --seed a1b2c3d4e5f60001" in response["run"]
    assert "--upstream-port 8799 --model capture" in response["run"]
    assert "--validate" in response["run"]
    assert '--only "$RESPONSE_POLICY"' in response["run"]
    assert "trap cleanup EXIT" in response["run"]
    # A runner is slower than the workstation that measured the rows; the deadline only
    # decides which cases become inconclusive.
    assert response["env"]["V2_CLIENT_READ_TIMEOUT"] == "900"
    # What the job prints about a gateway error is the exception type, never the log line.
    assert "grep -oE '^gateway error: [A-Za-z0-9_.]+'" in response["run"]
    gateway = (ROOT / "benchmarks/llm-guard-v2-profile/gateway.py").read_text(encoding="utf-8")
    assert 'print(f"gateway error: {type(exc).__name__}", file=sys.stderr, flush=True)' in gateway

    verify = next(step for step in steps if step.get("id") == "verify")
    assert verify["env"]["BENCHMARK_PYTHON"] == "${{ steps.operator.outputs.python-path }}"
    # An INVALID report is incomplete evidence: the instrument exits 0 on it, so verify checks.
    assert "benchmark-instrument/spec/v2.0.0/http-profile.schema.json" in verify["run"]
    assert "Draft202012Validator(schema).iter_errors(report)" in verify["run"]
    assert "error.json_path" in verify["run"] and "error.message" not in verify["run"]
    assert "os.environ['RESPONSE_POLICY'] + '.json'" in verify["run"]
    assert 'build_segments("a1b2c3d4e5f60001")' in verify["run"]
    assert "seeded_fixture(contract['seed']" in verify["run"]
    assert "get('self_probe')" in verify["run"]
    assert "not in ('CLEAN', 'LEAK')" in verify["run"]

    upload = next(step for step in steps if step.get("id") == "upload")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["name"] == "${{ env.ARTIFACT_NAME }}"
    assert upload["if"] == "always() && steps.verify.outcome == 'success'"
    paths = upload["with"]["path"].splitlines()
    assert any(path.endswith("/${{ env.RESPONSE_POLICY }}.json") for path in paths)
    assert any(path.endswith("/current.json") for path in paths)
    assert any(path.endswith("/source-identity.json") for path in paths)
    assert any(path.endswith("/verification.txt") for path in paths)
    assert any(path.endswith("/installed-packages.txt") for path in paths)
    assert not any(path.endswith(".log") for path in paths)

    assert steps[-1]["if"] == "always() && steps.result.outputs.status != 'clean'"
    assert "benchmarks/results/" not in text
    assert "secrets." not in text
