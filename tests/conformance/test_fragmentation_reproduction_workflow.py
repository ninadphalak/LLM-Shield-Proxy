"""Tracks 1 and 2 as one GitHub Actions job, runnable from a fork with nothing installed.

The workflow must run exactly what the page tells a reader to run, against the Presidio image
the page pins, and its summary must hand the result back through the independent-reproduction
form with the right boxes filled. The digest and the form's field ids are pinned here so the
page, the checker, the CI job and the workflow cannot drift apart one file at a time.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "fragmentation-reproduction.yml"
BENCHMARK_CI = ROOT / ".github" / "workflows" / "benchmark.yml"
PAGE = ROOT / "website" / "docs" / "conformance" / "reproduce-fragmentation.md"
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "independent-reproduction.yml"
DIGEST = re.compile(r"mcr\.microsoft\.com/presidio-analyzer@sha256:[0-9a-f]{64}")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPRODUCED = "RESULT: all 2 policies reproduced the published reports."
TABLE = """
=== running {policy}
{policy}  fidelity=1.0  leak_single=0.125  leak_adv=1.0  DeltaFrag=0.875  n=32/32  outcome=fail  schema=-
=== {policy} finished in 33.3s

=== {policy}
                                published            your run  match
Fidelity                              1.0                 1.0  yes
Leak, single chunk                  0.125               0.125  yes
DeltaFrag                           0.875               0.875  yes
Inspector digest         94262e29a492ab6a    94262e29a492ab6a  yes

  Every field matched except the host, timestamp and timing fields.
"""


def _checker() -> Any:
    path = ROOT / "benchmarks" / "reproduce_fragmentation.py"
    spec = importlib.util.spec_from_file_location("reproduce_fragmentation_for_workflow", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["reproduce"]["steps"]


def _step(step_id: str) -> dict[str, Any]:
    return next(step for step in _steps() if step.get("id") == step_id)


def _template_fields() -> dict[str, bool]:
    """Field id -> required, straight from the issue form."""
    form = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    return {
        field["id"]: bool((field.get("validations") or {}).get("required"))
        for field in form["body"]
        if "id" in field
    }


def test_the_presidio_digest_is_one_digest_on_the_page_in_ci_in_the_checker_and_here():
    found = {
        "workflow": set(DIGEST.findall(WORKFLOW.read_text(encoding="utf-8"))),
        "page": set(DIGEST.findall(PAGE.read_text(encoding="utf-8"))),
        "benchmark.yml": set(DIGEST.findall(BENCHMARK_CI.read_text(encoding="utf-8"))),
        "checker": {_checker().PRESIDIO_IMAGE},
    }
    assert all(len(digests) == 1 for digests in found.values()), found
    assert len(set.union(*found.values())) == 1, found


def test_the_workflow_runs_from_a_fork_with_no_inputs_and_no_secrets():
    workflow = _workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow["on"]
    assert not workflow["on"]["workflow_dispatch"], "no box to fill: the ref is the whole input"
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    checkout = _steps()[0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert "ref" not in (checkout.get("with") or {}), "the dispatched ref is the checkout"
    assert checkout["with"]["persist-credentials"] == "false"


def test_the_job_installs_and_runs_exactly_what_the_page_says():
    page = PAGE.read_text(encoding="utf-8")
    install = _step("install")["run"]
    assert 'python -m pip install "./pii-leak-benchmark[validate]"' in page
    assert 'python -m pip install "./pii-leak-benchmark[validate]"' in install
    assert "llm_shield_proxy" not in install and "pip install ." not in install
    python = next(step for step in _steps() if step.get("uses", "").startswith("actions/setup-python@"))
    assert python["with"]["python-version"] == "3.12"
    assert "3.12" in page

    assert "python benchmarks/reproduce_fragmentation.py --out reproduction" in page
    assert "python -u benchmarks/reproduce_fragmentation.py --out reproduction " in _step("track1")["run"]
    track2_command = (
        "python -u benchmarks/reproduce_fragmentation.py "
        "--policies presidio-chunk-local,presidio-retention --out reproduction-presidio "
    )
    assert track2_command in _step("track2")["run"]
    assert "--policies presidio-chunk-local,presidio-retention --out reproduction-presidio" in page

    presidio = _step("presidio")["run"]
    assert '"$PRESIDIO_IMAGE"' in presidio
    assert "/analyze" in presidio, "wait for the analyzer to answer, not just to listen"
    assert _workflow()["jobs"]["reproduce"]["env"]["PRESIDIO_IMAGE"] == _checker().PRESIDIO_IMAGE

    upload = next(step for step in _steps() if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    paths = upload["with"]["path"].splitlines()
    assert paths == ["reproduction/", "reproduction-presidio/", "track1-output.txt", "track2-output.txt"]


def test_the_expected_result_line_is_the_one_the_checker_prints():
    source = (ROOT / "benchmarks" / "reproduce_fragmentation.py").read_text(encoding="utf-8")
    assert 'f"RESULT: all {len(policies)} policies reproduced the published reports."' in source
    assert _workflow()["jobs"]["reproduce"]["env"]["EXPECTED_RESULT"] == REPRODUCED
    assert _template_fields()["track1"] and _template_fields()["track2"] is False
    form = TEMPLATE.read_text(encoding="utf-8")
    assert form.count(REPRODUCED) == 2, "the form's placeholders show the line the job checks for"


def _summary(tmp_path: Path, track1: str, track2: str, *, exits: tuple[str, str] = ("0", "0")):
    (tmp_path / "track1-output.txt").write_text(track1, encoding="utf-8")
    (tmp_path / "track2-output.txt").write_text(track2, encoding="utf-8")
    output = tmp_path / "output.txt"
    output.write_text("")
    step = _step("summary")
    script = step["run"].split("<<'PY' | tee -a \"$GITHUB_STEP_SUMMARY\"\n", 1)[1].rsplit("PY\n", 1)[0]
    env = dict(
        os.environ,
        GITHUB_OUTPUT=str(output),
        GITHUB_SHA=COMMIT,
        GITHUB_SERVER_URL="https://github.com",
        GITHUB_REPOSITORY="friend/LLM-Shield-Proxy",
        GITHUB_RUN_ID="4242",
        RUNNER_DESCRIPTION="Ubuntu 24.04.3 LTS",
        PYTHON_VERSION="Python 3.12.12",
        TRACK1_EXIT=exits[0],
        TRACK2_EXIT=exits[1],
        EXPECTED_RESULT=REPRODUCED,
        ARTIFACT_NAME="fragmentation-reproduction",
        PRESIDIO_IMAGE=_workflow()["jobs"]["reproduce"]["env"]["PRESIDIO_IMAGE"],
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=True
    )
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return outputs, finished.stdout


def _link(summary: str) -> str:
    match = re.search(r"\((https://github\.com/ninadphalak/LLM-Shield-Proxy/issues/new\?[^)]+)\)", summary)
    assert match, summary
    return match.group(1)


def test_a_green_run_fills_the_form_from_the_run(tmp_path):
    track1 = TABLE.format(policy="chunk-local") + TABLE.format(policy="bounded-retention") + REPRODUCED + "\n"
    track2 = TABLE.format(policy="presidio-chunk-local") + REPRODUCED + "\n"
    outputs, summary = _summary(tmp_path, track1, track2)
    assert outputs["reproduced"] == "true"
    assert summary.count(REPRODUCED) >= 2
    for policy in ("chunk-local", "bounded-retention", "presidio-chunk-local"):
        assert f"### `{policy}`" in summary
    assert "| Leak, single chunk | 0.125 | 0.125 | yes |" in summary
    assert COMMIT in summary and "Ubuntu 24.04.3 LTS" in summary and "Python 3.12.12" in summary
    assert "expire" in summary and "durable" in summary

    link = _link(summary)
    assert len(link) < 6000
    query = parse_qs(urlparse(link).query)
    fields = _template_fields()
    filled = {key for key in query if key not in ("template", "title")}
    assert filled <= set(fields), filled - set(fields)
    assert query["template"] == ["independent-reproduction.yml"]
    assert query["commit"] == [COMMIT]
    assert query["environment"] == ["Ubuntu 24.04.3 LTS (GitHub-hosted runner), Python 3.12.12"]
    assert query["track1"] == [REPRODUCED] and query["track2"] == [REPRODUCED]
    assert "https://github.com/friend/LLM-Shield-Proxy/actions/runs/4242" in query["files"][0]
    # Every required box the run can answer is answered; only the submitter's name is left.
    assert {key for key, required in fields.items() if required} - filled == {"who"}


def test_a_mismatch_is_reported_with_the_line_that_says_so_and_still_offered(tmp_path):
    failed = "RESULT: 1 of 2 policies did NOT reproduce."
    track1 = TABLE.format(policy="chunk-local") + REPRODUCED + "\n"
    track2 = TABLE.format(policy="presidio-chunk-local") + failed + "\n"
    outputs, summary = _summary(tmp_path, track1, track2, exits=("0", "1"))
    assert outputs["reproduced"] == "false"
    assert failed in summary
    assert parse_qs(urlparse(_link(summary)).query)["track2"] == [failed]


def test_a_missing_result_line_is_never_a_pass(tmp_path):
    outputs, summary = _summary(tmp_path, "Traceback (most recent call last):\n", "", exits=("1", "1"))
    assert outputs["reproduced"] == "false"
    assert "no RESULT line" in summary
    query = parse_qs(urlparse(_link(summary)).query)
    assert query["track1"] == ["no RESULT line (exit 1)"]


def test_the_job_fails_on_anything_but_two_reproduced_lines():
    fail = _steps()[-1]
    assert fail["if"] == "always()"
    assert fail["env"]["REPRODUCED"] == "${{ steps.summary.outputs.reproduced }}"
    assert 'test "$REPRODUCED" = true' in fail["run"]
