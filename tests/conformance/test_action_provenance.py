import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "pii-leak-benchmark" / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
BENCHMARK = ROOT / ".github" / "workflows" / "benchmark.yml"
DOCKER_PUBLISH = ROOT / ".github" / "workflows" / "docker-publish.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SUBMITTING = ROOT / "website" / "docs" / "conformance" / "submitting.md"

ATTEST_V4_SHA = "1e69f48acb82d1966a394da916b4c1698aa569d6"
UPLOAD_ARTIFACT_V7_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


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
