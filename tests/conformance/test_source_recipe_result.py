"""The last word of every proxy source recipe: one verdict, and a submission that needs no typing.

A friend running a recipe in a fork reads the job summary, not the workflow. These tests run
each recipe's own result step against synthetic bundles and feed the link it prints back
through the wall intake, so the words on the page and the parser that reads them cannot drift.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RECIPES = {
    "source-reproduction.yml": ("source-response-on.json", "source-response-off.json"),
    "portkey-source-reproduction.yml": ("portkey-source.json",),
    "litellm-source-reproduction.yml": ("litellm-source.json",),
    "nemo-source-reproduction.yml": ("nemo-source.json",),
}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
RUN = "https://github.com/friend/proxy/actions/runs/4242"


def _load_intake():
    spec = importlib.util.spec_from_file_location(
        "intake_for_recipes", ROOT / "scripts" / "process_conformance_submission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intake = _load_intake()


def _job(name):
    workflow = yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["jobs"]["reproduce"]


def _result_step(name):
    return next(step for step in _job(name)["steps"] if step.get("id") == "result")


def _script(name):
    run = _result_step(name)["run"]
    return run.split("<<'PY' >> \"$GITHUB_STEP_SUMMARY\"\n", 1)[1].rsplit("PY\n", 1)[0]


def _response(single, split, inconclusive=0):
    return {"metrics": {"leak_rate": {"single_chunk": single, "adversarial": split},
                        "cases_by_condition": {"single_chunk": 16, "adversarial": 16},
                        "cases_scored": 32, "cases_inconclusive": inconclusive}}


def _run(tmp_path, name, *, verified=True, request_leak=False, response_leak=False,
         off_arm_leak=True, response_outcome="success", inconclusive=0, reports=None,
         upload_outcome="success", empty_condition=False):
    root = tmp_path / "bundle"
    root.mkdir()
    entities = {"EMAIL": "leak" if request_leak else "contained", "SSN": "contained"}
    (root / "current.json").write_text(json.dumps({"verdict": "LEAK" if request_leak else "CLEAN",
                                                   "entities": entities}))
    for report in (RECIPES[name] if reports is None else reports):
        leaking = off_arm_leak if report.endswith("-off.json") else response_leak
        body = _response(0.5 if leaking else 0.0, 1.0 if leaking else 0.0,
                         0 if report.endswith("-off.json") else inconclusive)
        if empty_condition and not report.endswith("-off.json"):
            body["metrics"]["cases_by_condition"]["adversarial"] = 0
            body["metrics"]["leak_rate"]["adversarial"] = 0.0
        (root / report).write_text(json.dumps(body))
    (root / "source-identity.json").write_text(json.dumps(
        {"schema": "pii-leak-benchmark/source-build/v1", "source_commit": COMMIT, "source_selector": "v9.9.9"}))
    if verified:
        (root / "verification.txt").write_text("ok\n")
    output = tmp_path / "output.txt"
    output.write_text("")
    job_env = {key: value for key, value in _job(name)["env"].items() if key.startswith("WALL_")}
    env = dict(os.environ, **job_env, REPORT_DIR=str(root), GITHUB_OUTPUT=str(output),
               VERIFY_OUTCOME="success" if verified else "failure",
               OPERATOR_OUTCOME="failure" if request_leak else "success",
               RESPONSE_OUTCOME=response_outcome,
               UPLOAD_OUTCOME=upload_outcome,
               STEP_OUTCOMES="request=success, response=failure, verification=failure",
               GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="friend/proxy",
               GITHUB_RUN_ID="4242")
    finished = subprocess.run([sys.executable, "-c", _script(name)], env=env, capture_output=True,
                              text=True, check=True)
    status = dict(line.split("=", 1) for line in output.read_text().splitlines())["status"]
    return status, finished.stdout


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_an_incomplete_run_says_so_and_offers_nothing_to_submit(tmp_path, name):
    status, summary = _run(tmp_path, name, verified=False)
    assert status == "incomplete"
    assert "INCOMPLETE, do not submit" in summary
    assert "issues/new" not in summary and "gh issue create" not in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_request_leak_is_a_measurement_with_a_complete_submission(tmp_path, name):
    status, summary = _run(tmp_path, name, request_leak=True)
    assert status == "leak"
    assert "MEASURED LEAK" in summary and "not because the run broke" in summary
    link = re.search(r"\((https://github\.com/ninadphalak/LLM-Shield-Proxy/issues/new\?[^)]+)\)", summary)
    query = parse_qs(urlparse(link.group(1)).query)
    fields = intake.parse_submission(query["body"][0])
    # The link alone passes intake validation: nothing is left for the submitter to type.
    assert intake.validate(fields) == []
    assert fields["run_url"] == RUN
    assert fields["gateway"] == _job(name)["env"]["WALL_PRODUCT"]
    assert fields["version"].startswith("v9.9.9 (commit 0123456789ab), ")
    assert query["title"] == [f"Result: {fields['gateway']}"]
    command = re.search(r"^gh issue create .*$", summary, flags=re.MULTILINE).group(0)
    assert f'--body "{RUN}"' in command
    assert intake.run_url_from_body(RUN) == RUN


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_response_leak_alone_turns_the_run_red(tmp_path, name):
    status, summary = _run(tmp_path, name, response_leak=True)
    assert status == "leak"
    assert "8 of 16 leaked" in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_clean_measurement_is_green_and_still_submittable(tmp_path, name):
    status, summary = _run(tmp_path, name)
    assert status == "clean"
    assert "MEASURED CLEAN" in summary
    assert "Submit this run to the results wall" in summary


def test_the_shield_off_arm_is_a_control_and_its_leak_does_not_count(tmp_path):
    status, summary = _run(tmp_path, "source-reproduction.yml", off_arm_leak=True)
    assert status == "clean"
    assert "source-response-off (control, redaction off)" in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_only_a_clean_result_leaves_the_job_green(name):
    steps = _job(name)["steps"]
    assert steps[-1]["if"] == "always() && steps.result.outputs.status != 'clean'"
    assert _result_step(name)["if"] == "always()"


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_bundle_names_itself_so_a_bare_run_link_is_enough(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    for key, env in (("product", "WALL_PRODUCT"), ("configuration", "WALL_CONFIGURATION"),
                     ("license", "WALL_LICENSE"), ("project_url", "WALL_PROJECT_URL")):
        assert f"'{key}': os.environ['{env}']" in text, (name, key)
        assert _job(name)["env"][env].strip(), (name, env)


def test_the_four_result_steps_are_one_step():
    """A fix to one copy must reach the others; only the report directory differs."""
    bodies = {name: yaml.safe_dump({k: v for k, v in _result_step(name).items() if k != "env"})
              for name in RECIPES}
    assert len(set(bodies.values())) == 1


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_an_all_inconclusive_response_is_not_a_clean_measurement(tmp_path, name):
    """No applicable case means nothing was shown clean: the job must not go green."""
    status, summary = _run(tmp_path, name, inconclusive=32)
    assert status == "incomplete"
    assert "MEASURED CLEAN" not in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_failed_response_step_is_not_clean_even_with_a_report(tmp_path, name):
    status, _ = _run(tmp_path, name, response_outcome="failure")
    assert status == "incomplete"


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_missing_response_report_is_not_clean(tmp_path, name):
    status, _ = _run(tmp_path, name, reports=[r for r in RECIPES[name] if r.endswith("-off.json")])
    assert status == "incomplete"


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_leak_in_an_incomplete_run_is_reported_as_incomplete(tmp_path, name):
    """The summary calls a measured result complete; a partly unmeasured run is not."""
    status, summary = _run(tmp_path, name, request_leak=True, inconclusive=32)
    assert status == "incomplete"
    assert "MEASURED LEAK" not in summary and "issues/new" not in summary


def test_some_inconclusive_cases_still_allow_a_clean_measurement(tmp_path):
    """NeMo refuses some cases by design; the applicable ones are still a measurement."""
    status, summary = _run(tmp_path, "nemo-source-reproduction.yml", inconclusive=8)
    assert status == "clean"
    assert "| 8 |" in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_a_condition_with_no_applicable_case_is_not_measured(tmp_path, name):
    status, _ = _run(tmp_path, name, empty_condition=True)
    assert status == "incomplete"


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_no_submission_is_offered_when_the_evidence_did_not_upload(tmp_path, name):
    """The intake reads the artifact; a link to a run without one cannot be published."""
    status, summary = _run(tmp_path, name, request_leak=True, upload_outcome="failure")
    assert status == "incomplete"
    assert "issues/new" not in summary and "gh issue create" not in summary


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_result_step_reads_the_upload_outcome(name):
    job = _job(name)
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
    assert upload.get("id") == "upload"
    assert _result_step(name)["env"]["UPLOAD_OUTCOME"] == "${{ steps.upload.outcome }}"
