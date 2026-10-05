import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "pii-leak-benchmark" / "action.yml"
ROOT_ACTION = ROOT / "action.yml"
ACTION_README = ROOT / ".github" / "actions" / "README.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
BENCHMARK = ROOT / ".github" / "workflows" / "benchmark.yml"
DOCKER_PUBLISH = ROOT / ".github" / "workflows" / "docker-publish.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SUBMITTING = ROOT / "website" / "docs" / "conformance" / "submitting.md"

ATTEST_V4_SHA = "1e69f48acb82d1966a394da916b4c1698aa569d6"
UPLOAD_ARTIFACT_V7_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"

ROOT_ACTION_INPUTS = {
    "target-base-url": (True, None),
    "start-command": (False, ""),
    "upstream-env": (False, ""),
    "target-model": (False, "conformance-model"),
    "target-version": (False, "current"),
    "duty": (False, "restore"),
    "profile": (False, "pii-secrets-v1"),
    "seed": (False, "gateway-ci-v1"),
    "baseline-base-url": (False, ""),
    "baseline-start-command": (False, ""),
    "baseline-version": (False, "baseline"),
    "baseline-report": (False, ""),
    "artifact-name": (False, "pii-leak-benchmark"),
    "submission-section": (False, "true"),
    # Off by default: the reports are uploaded, and a crashing start command may print the
    # secrets it was given (review on #174).
    "startup-log": (False, "false"),
}

RESEARCH_ACTION_INPUTS = {
    "target-base-url": (True, None),
    "target-name": (False, "external-openai-compatible-endpoint"),
    "target-version": (False, "unspecified"),
    "target-api-key": (False, "conformance-key"),
    "target-model": (False, "conformance-model"),
    "target-header": (False, ""),
    "iterations": (False, "3"),
    "capture-host": (False, "127.0.0.1"),
    "capture-port": (False, "8765"),
    "capture-public-url": (False, ""),
    "capture-token": (False, ""),
    "redaction-claimed": (False, "unknown"),
    "redaction-claim-citation": (False, ""),
    "redaction-claim-quote": (False, ""),
    "redaction-enabled": (False, "false"),
    "redaction-config-reference": (False, ""),
    "python-version": (False, "3.12"),
    "source": (False, "pii-leak-benchmark"),
    "json-out": (False, "pii-leak-benchmark-report.json"),
    "artifact-name": (False, "pii-leak-benchmark-report"),
    "fail-on-non-pass": (False, "true"),
    "attest-report": (False, "false"),
    "profiles": (False, "operator"),
    "response-seed": (False, "a1b2c3d4e5f60001"),
    "response-capture-port": (False, "8799"),
    "response-out": (False, "pii-leak-benchmark-response"),
}


def _assert_input_contract(document, expected):
    assert set(document["inputs"]) == set(expected)
    for name, (required, default) in expected.items():
        metadata = document["inputs"][name]
        assert bool(metadata.get("required", False)) is required, name
        if default is None:
            assert "default" not in metadata, name
        else:
            assert metadata.get("default") == default, name


def test_public_action_input_and_output_contracts_are_pinned():
    operator = yaml.safe_load(ROOT_ACTION.read_text(encoding="utf-8"))
    research = yaml.safe_load(ACTION.read_text(encoding="utf-8"))

    _assert_input_contract(operator, ROOT_ACTION_INPUTS)
    _assert_input_contract(research, RESEARCH_ACTION_INPUTS)

    assert operator["outputs"] == {
        "report-directory": {
            "description": "Directory with operator reports, raw measurements and summary.md",
            "value": "${{ steps.setup.outputs.out }}",
        },
        "python-path": {
            "description": "Python interpreter with the pinned operator harness installed",
            "value": "${{ steps.setup.outputs.python }}",
        },
    }
    keep = next(step for step in operator["runs"]["steps"] if step.get("name") == "Keep benchmark reports")
    assert "inputs.artifact-name != ''" in keep["if"]
    assert research["outputs"] == {
        "outcome": {
            "description": (
                "What a published row may say: pass, fail, no-leak-profile-not-met, "
                "not-applicable, redaction-not-enabled, inconclusive, or claim-unstated."
            ),
            "value": "${{ steps.run.outputs.outcome }}",
        },
        "passed": {
            "description": "The raw measurement: did every check pass.",
            "value": "${{ steps.run.outputs.passed }}",
        },
        "leaked-entity-types": {
            "description": (
                "Comma-separated entity types that reached the capture. Empty on a clean run."
            ),
            "value": "${{ steps.run.outputs.leaked }}",
        },
        "report-path": {
            "description": "Path to the raw report.",
            "value": "${{ steps.run.outputs.report }}",
        },
        "attestation-url": {
            "description": (
                "Verification page for the detached report attestation; empty when "
                "attest-report is false."
            ),
            "value": "${{ steps.attest.outputs.attestation-url }}",
        },
        "response-report-path": {
            "description": (
                "Path to the response-split report; empty when profile is operator."
            ),
            "value": "${{ steps.response.outputs.report }}",
        },
        "response-leaked": {
            "description": (
                "true when the response profile saw a value reach the client, whole or split; "
                "false when it did not; incomplete when no case was applicable, so nothing was "
                "measured; empty when profile is operator."
            ),
            "value": "${{ steps.response.outputs.leaked }}",
        },
        "response-applicable": {
            "description": "Cases the response profile scored; 0 means the run measured nothing.",
            "value": "${{ steps.response.outputs.applicable }}",
        },
        "response-inconclusive": {
            "description": (
                "Cases the response profile could not score (timeout, error, or no complete response)."
            ),
            "value": "${{ steps.response.outputs.inconclusive }}",
        },
    }

    assert operator["inputs"]["start-command"]["description"] == (
        "Optional foreground command to start the gateway; stopped after measurement"
    )
    assert operator["inputs"]["upstream-env"]["description"] == (
        "Gateway environment variable that receives the benchmark capture URL"
    )
    assert research["inputs"]["target-version"]["description"] == (
        "Exact version or image digest measured. A row without this is unpublishable."
    )
    assert research["inputs"]["target-api-key"]["description"] == (
        "Credential the target expects. Pass a secret; it is never written to the report."
    )
    assert research["inputs"]["attest-report"]["description"] == (
        "Create detached GitHub/Sigstore provenance over the raw report. The caller must "
        "grant id-token: write and attestations: write. Required for a submitted run to "
        "count toward the independent-replication floor."
    )


def test_action_roles_are_documented_without_conflating_attestation():
    note = ACTION_README.read_text(encoding="utf-8")

    assert "supported operator CI and regression" in note
    assert "legacy research HTTP profile" in note
    assert "detached attestation" in note
    assert "does not create a detached attestation" in note


def test_submission_short_path_is_automatic_and_artifact_derived():
    instructions = SUBMITTING.read_text(encoding="utf-8")
    short_path = instructions.split("### The short path, for a results-wall row", 1)[1].split(
        "## CI Automation", 1
    )[0]
    normalized = " ".join(short_path.split())

    assert "A person then fills in the measurement columns" not in short_path
    assert (
        "The intake downloads the linked public run’s recognized report artifact, derives the "
        "measurement columns from its JSON reports, builds the site, and publishes the row "
        "through a controlled pull request."
    ) in normalized
    assert "bundle-manifest" not in short_path


def test_composite_action_attests_the_finished_report_as_a_detached_subject():
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))

    assert document["inputs"]["attest-report"]["default"] == "false"
    assert document["outputs"]["attestation-url"]["value"] == "${{ steps.attest.outputs.attestation-url }}"

    attest = next(step for step in document["runs"]["steps"] if step.get("id") == "attest")
    assert attest["uses"] == f"actions/attest@{ATTEST_V4_SHA}"
    assert attest["if"] == "${{ inputs.attest-report == 'true' }}"
    assert attest["with"]["subject-path"] == "${{ inputs.json-out }}"


def test_composite_action_never_interpolates_inputs_into_shell_source():
    """Action inputs are data, not Bash source -- including multiline headers."""
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))

    shell_steps = [step for step in document["runs"]["steps"] if "run" in step]
    assert shell_steps
    for step in shell_steps:
        assert "${{ inputs." not in step["run"], step["name"]


def test_composite_action_keeps_target_credentials_out_of_process_arguments():
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    run_step = next(step for step in document["runs"]["steps"] if step.get("id") == "run")

    assert run_step["env"]["CONFORMANCE_TARGET_API_KEY"] == "${{ inputs.target-api-key }}"
    assert "--target-api-key" not in run_step["run"]
    assert run_step["env"]["CONFORMANCE_TARGET_HEADERS"] == "${{ inputs.target-header }}"
    assert "--target-header" not in run_step["run"]
    assert run_step["env"]["CONFORMANCE_CAPTURE_TOKEN"] == "${{ inputs.capture-token }}"


def test_composite_action_passes_install_source_and_report_paths_through_env():
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    install = next(step for step in document["runs"]["steps"] if step["name"] == "Install the harness")
    run_step = next(step for step in document["runs"]["steps"] if step.get("id") == "run")
    summary = next(step for step in document["runs"]["steps"] if step["name"] == "Summarise")

    assert install["env"]["HARNESS_SOURCE"] == "${{ inputs.source }}"
    assert run_step["env"]["INPUT_JSON_OUT"] == "${{ inputs.json-out }}"
    assert summary["env"]["INPUT_JSON_OUT"] == "${{ inputs.json-out }}"


def test_every_remote_action_is_pinned_to_an_immutable_commit():
    offenders = []
    action_files = sorted((ROOT / ".github").rglob("*.yml"))
    for path in action_files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match or match.group(1).startswith("./"):
                continue
            reference = match.group(1).rsplit("@", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{40}", reference):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {match.group(1)}")

    assert not offenders, "remote actions with mutable refs:\n  " + "\n  ".join(offenders)


def test_composite_action_runs_the_response_profile_only_when_asked():
    """`profile` adds the response-split profile after the operator run; operator stays the default."""
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    steps = document["runs"]["steps"]
    run_step = next(step for step in steps if step.get("id") == "run")
    response = next(step for step in steps if step.get("id") == "response")
    response_summary = next(step for step in steps if step["name"] == "Summarise the response profile")
    upload = next(step for step in steps if step["name"] == "Upload the raw report")
    fail = next(step for step in steps if step["name"] == "Fail on a non-pass")

    assert document["inputs"]["profiles"]["default"] == "operator"
    assert run_step["if"] == "${{ inputs.profiles != 'response' }}"
    assert response["if"] == "${{ inputs.profiles == 'response' || inputs.profiles == 'both' }}"
    assert response_summary["if"] == response["if"]
    # The operator run comes first: the response step is after it in the step list.
    assert steps.index(response) > steps.index(run_step)

    assert response["env"]["INPUT_TARGET_BASE_URL"] == "${{ inputs.target-base-url }}"
    assert response["env"]["INPUT_TARGET_NAME"] == "${{ inputs.target-name }}"
    assert response["env"]["INPUT_TARGET_MODEL"] == "${{ inputs.target-model }}"
    assert response["env"]["INPUT_RESPONSE_SEED"] == "${{ inputs.response-seed }}"
    assert response["env"]["INPUT_RESPONSE_CAPTURE_PORT"] == "${{ inputs.response-capture-port }}"
    assert response["env"]["INPUT_RESPONSE_OUT"] == "${{ inputs.response-out }}"
    assert response["env"]["INPUT_REDACTION_ENABLED"] == "${{ inputs.redaction-enabled }}"
    # Credentials and headers reach the instrument through the environment, never argv.
    assert response["env"]["V2_GATEWAY_TOKEN"] == "${{ inputs.target-api-key }}"
    assert response["env"]["INPUT_TARGET_HEADERS"] == "${{ inputs.target-header }}"
    assert "pii-leak-benchmark-v2" in response["run"]
    for flag in ("--validate", "--json-out", "--oracle midpoint", "--seed", "--upstream-port", "--only"):
        assert flag in response["run"], flag
    assert "capture://self" in response["run"] and "passthrough" in response["run"]
    assert "/chat/completions" in response["run"]

    assert "DeltaFrag" in response_summary["run"]
    assert response_summary["env"]["INPUT_RESPONSE_OUT"] == "${{ inputs.response-out }}"
    paths = upload["with"]["path"].splitlines()
    assert "${{ inputs.json-out }}" in paths
    assert "${{ inputs.response-out }}" in paths
    assert fail["env"]["OPERATOR_OUTCOME"] == "${{ steps.run.outcome }}"
    assert fail["env"]["RESPONSE_LEAKED"] == "${{ steps.response.outputs.leaked }}"
    # An all-inconclusive run is a non-pass, never a clean one.
    assert '"$RESPONSE_LEAKED" = "incomplete"' in fail["run"]
    assert document["outputs"]["response-applicable"]["value"] == "${{ steps.response.outputs.applicable }}"
    assert document["outputs"]["response-inconclusive"]["value"] == "${{ steps.response.outputs.inconclusive }}"


def _response_output_script() -> str:
    """The Python the response step runs to derive its outputs, lifted from the heredoc."""
    text = ACTION.read_text(encoding="utf-8")
    start = text.index("        import json, sys\n        rows = json.load(open(sys.argv[1]")
    end = text.index("        PY\n", start)
    return "\n".join(line[8:] for line in text[start:end].splitlines())


@pytest.mark.parametrize(
    ("rows", "metrics", "expected"),
    [
        # Every case inconclusive: the rates are 0.0 by construction, which is not a measurement.
        ([{"leak_single_chunk": 0.0, "leak_adversarial": 0.0}], {"cases_applicable": 0, "cases_inconclusive": 32}, "incomplete"),
        ([], {"cases_applicable": 32, "cases_inconclusive": 0}, "incomplete"),
        ([{"leak_single_chunk": 0.0, "leak_adversarial": 0.0}], {"cases_applicable": 32, "cases_inconclusive": 0}, "false"),
        ([{"leak_single_chunk": 0.0, "leak_adversarial": 0.0625}], {"cases_applicable": 30, "cases_inconclusive": 2}, "true"),
    ],
)
def test_response_profile_output_reads_incomplete_when_nothing_was_scored(tmp_path, rows, metrics, expected):
    summary = tmp_path / "response-summary.json"
    report = tmp_path / "policy.json"
    summary.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    report.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-", str(summary), str(report)],
        input=_response_output_script(),
        capture_output=True,
        text=True,
        check=True,
    )
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert outputs["leaked"] == expected
    assert outputs["applicable"] == str(metrics["cases_applicable"])
    assert outputs["inconclusive"] == str(metrics["cases_inconclusive"])


def test_main_ci_proves_the_response_profile_on_the_negative_control():
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = workflow["jobs"]["benchmark-action"]
    control = next(step for step in job["steps"] if step.get("id") == "control")
    assert control["with"]["profiles"] == "both"
    assert control["with"]["response-capture-port"] == "0"
    proof = next(step for step in job["steps"] if step["name"].startswith("The response profile"))
    assert proof["env"]["RESPONSE_REPORT"] == "${{ steps.control.outputs.response-report-path }}"
    assert proof["env"]["RESPONSE_LEAKED"] == "${{ steps.control.outputs.response-leaked }}"
    assert "spec/v2.0.0/http-profile.schema.json" in proof["run"]
    assert "Draft202012Validator" in proof["run"]


def test_main_ci_exercises_attestation_without_granting_write_tokens_to_pull_requests():
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = workflow["jobs"]["benchmark-action"]

    assert job["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    run_step = next(step for step in job["steps"] if step.get("id") == "control")
    assert run_step["with"]["attest-report"] == "${{ github.event_name == 'push' && 'true' || 'false' }}"
    proof_step = next(step for step in job["steps"] if step["name"].startswith("A main-branch run"))
    assert proof_step["if"] == "${{ github.event_name == 'push' }}"


def test_public_benchmark_workflow_recomputes_published_evidence_from_raw_reports():
    workflow = yaml.safe_load(BENCHMARK.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["evidence-integrity"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    upload = next(
        step
        for step in steps
        if step.get("uses") == f"actions/upload-artifact@{UPLOAD_ARTIFACT_V7_SHA}"
    )

    assert "tests/conformance/test_published_profiles.py" in commands
    assert "tests/conformance/test_fide_partition_oracle.py" in commands
    assert "benchmarks/fide_uncertainty.py" in commands
    assert "benchmarks/fide_numeric_audit.py" in commands
    assert upload["with"]["name"] == "published-evidence-analysis"


def test_submission_instructions_require_verifiable_detached_provenance():
    instructions = SUBMITTING.read_text(encoding="utf-8")

    assert "attest-report: \"true\"" in instructions
    assert "gh attestation verify pii-leak-benchmark-report.json -R submitter/repository" in instructions
    assert "A result only achieves \"replicated\" status when three separate individuals" in instructions
    assert "does not definitively prove the remote gateway process used the exact version stated" in instructions


def test_companion_package_releases_cannot_publish_proxy_images_or_assets():
    release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    docker_publish = yaml.safe_load(DOCKER_PUBLISH.read_text(encoding="utf-8"))

    assert release["jobs"]["build-release-artifacts"]["if"] == (
        "startsWith(github.event.release.tag_name, 'v') && "
        "!contains(github.event.release.tag_name, 'evidence')"
    )
    assert docker_publish["jobs"]["build-sign-attest"]["if"] == (
        "github.event_name == 'workflow_dispatch' || "
        "(startsWith(github.event.release.tag_name, 'v') && "
        "!contains(github.event.release.tag_name, 'evidence'))"
    )


def test_an_evidence_release_cannot_publish_proxy_images_or_assets():
    """The `v` prefix is not specific enough, and the evidence tags share it.

    Evidence tags are named `v2-evidence-round-N`. Publishing a GitHub Release on one is
    the supported way to mint a Zenodo DOI for the measurement snapshot the manuscript
    cites, and `startsWith(tag, 'v')` on its own would let that Release build and
    GPG-sign proxy artifacts from an evidence commit and push a container image tagged
    `v2-evidence-round-8`. A release gate that fires on the wrong kind of tag is the same
    class of defect as a scope claim that outruns what was tested.
    """
    release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    docker_publish = yaml.safe_load(DOCKER_PUBLISH.read_text(encoding="utf-8"))

    for guard in (
        release["jobs"]["build-release-artifacts"]["if"],
        docker_publish["jobs"]["build-sign-attest"]["if"],
    ):
        assert "!contains(github.event.release.tag_name, 'evidence')" in guard
