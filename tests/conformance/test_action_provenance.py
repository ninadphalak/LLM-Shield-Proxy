from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "pii-leak-benchmark" / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKER_PUBLISH = ROOT / ".github" / "workflows" / "docker-publish.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SUBMITTING = ROOT / "website" / "docs" / "conformance" / "submitting.md"


def test_composite_action_attests_the_finished_report_as_a_detached_subject():
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))

    assert document["inputs"]["attest-report"]["default"] == "false"
    assert document["outputs"]["attestation-url"]["value"] == "${{ steps.attest.outputs.attestation-url }}"

    attest = next(step for step in document["runs"]["steps"] if step.get("id") == "attest")
    assert attest["uses"] == "actions/attest@v4"
    assert attest["if"] == "${{ inputs.attest-report == 'true' }}"
    assert attest["with"]["subject-path"] == "${{ inputs.json-out }}"


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


def test_submission_instructions_require_verifiable_detached_provenance():
    instructions = SUBMITTING.read_text(encoding="utf-8")

    assert "attest-report: \"true\"" in instructions
    assert "gh attestation verify pii-leak-benchmark-report.json -R submitter/repository" in instructions
    assert "counts toward the independent-replication floor only" in instructions
    assert "does not prove that the remotely measured process" in instructions


def test_companion_package_releases_cannot_publish_proxy_images_or_assets():
    release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    docker_publish = yaml.safe_load(DOCKER_PUBLISH.read_text(encoding="utf-8"))

    assert release["jobs"]["build-release-artifacts"]["if"] == (
        "startsWith(github.event.release.tag_name, 'v')"
    )
    assert docker_publish["jobs"]["build-sign-attest"]["if"] == (
        "github.event_name == 'workflow_dispatch' || "
        "startsWith(github.event.release.tag_name, 'v')"
    )
