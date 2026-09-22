from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.product_reproduction.schemas import schema_validator

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "spec" / "product-reproduction" / "v1"


def _schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return schema_validator(name.removesuffix(".schema.json"))


@pytest.mark.parametrize(
    "name",
    ["catalog.schema.json", "bundle-manifest.schema.json", "comparison.schema.json", "submission.schema.json"],
)
def test_product_reproduction_schemas_are_valid_draft_2020_12(name: str) -> None:
    schema = _schema(name)

    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].startswith("https://llmshieldproxy.com/spec/product-reproduction/v1/")


@pytest.fixture
def valid_documents() -> dict[str, dict[str, Any]]:
    digest = "a" * 64
    revision = "b" * 40
    return {
        "bundle-manifest.schema.json": {
            "schema": "pii-leak-benchmark/product-bundle/v1",
            "schema_version": "1.0.0",
            "target_id": "test-gateway-default",
            "mode": "reproduce",
            "suite": ["operator", "response-midpoint"],
            "started_at": "2026-09-21T12:00:00Z",
            "ended_at": "2026-09-21T12:01:00Z",
            "experiment_health": "complete",
            "product_results": {"operator": "CLEAN", "response_midpoint": "CLEAN"},
            "reproduction_status": {"operator": "not-compared", "response_midpoint": "matched"},
            "workflow": {
                "repository": "owner/repo",
                "workflow_ref": "owner/repo/.github/workflows/product-reproduction.yml@refs/heads/main",
                "run_id": "123",
                "run_attempt": 1,
                "actor": "operator",
                "commit_sha": revision,
                "branch": "main",
                "runner_image": "ubuntu-24.04",
                "runner_os": "Linux",
            },
            "harness": {
                "version": "0.1.0",
                "source_revision": revision,
                "instrument_digest": digest,
                "install_artifact_sha256": digest,
            },
            "product": {
                "project": "Test Gateway",
                "project_url": "https://example.test/gateway",
                "license": "Apache-2.0",
                "requested_release_selector": "test-gateway-1.2.3-default",
                "release_source": "test-official",
                "released_version": "1.2.3",
                "release_state": "stable",
                "artifact_reference": "test-gateway==1.2.3",
                "artifact_identity": "sha256:" + digest,
                "release_url": "https://example.test/gateway/releases/1.2.3",
                "configuration_id": "test-v1",
                "configuration_sha256": digest,
            },
            "profiles": {
                "seed": "a1b2c3d4e5f60001",
                "oracle": "midpoint",
                "iterations": 1,
            },
            "resources": {
                "declared": {
                    "runner_class": "standard-ubuntu",
                    "minimum_memory_mib": 4096,
                    "minimum_disk_mib": 8192,
                    "service_limits": [{"service": "gateway", "memory_mib": 1024}],
                },
                "observed": {
                    "available_memory_mib": 7000,
                    "available_disk_mib": 20000,
                    "peak_memory_mib": 900,
                },
            },
            "members": [{"path": "reports/control.raw.json", "sha256": digest, "size": 2}],
            "reports": {
                "control": "reports/control.raw.json",
                "operator": "reports/operator.current.json",
                "operator_raw": "reports/operator.current.raw.json",
                "response_midpoint": "reports/response-midpoint.json",
                "comparison": "reports/comparison.json",
            },
            "sanitization": {"policy": "product-reproduction/v1", "verified": True},
            "baseline": {
                "id": "test-gateway-1.2.3-default",
                "acceptance_status": "accepted",
                "comparison_policy": "product-reproduction/v1",
                "reports": {
                    "operator": None,
                    "response_midpoint": {
                        "path": "benchmarks/product_reproduction/baselines/response.json",
                        "sha256": digest,
                    },
                },
            },
            "limitations": ["One midpoint split per fixture."],
        },
        "comparison.schema.json": {
            "schema": "pii-leak-benchmark/product-comparison/v1",
            "policy": "product-reproduction/v1",
            "target_id": "test-gateway-default",
            "mode": "reproduce",
            "baseline_id": "test-gateway-1.2.3-default",
            "artifact_identity_match": True,
            "configuration_match": True,
            "profiles": {
                "operator": {
                    "status": "not-compared",
                    "level": "none",
                    "current_sha256": digest,
                    "differences": [],
                },
                "response-midpoint": {
                    "status": "matched",
                    "level": "primary",
                    "current_sha256": digest,
                    "baseline_sha256": digest,
                    "differences": [],
                },
            },
        },
        "submission.schema.json": {
            "schema": "pii-leak-benchmark/submission/v2",
            "gateway": "Test Gateway",
            "requested_release_selector": "test-gateway-1.2.3-default",
            "version": "1.2.3",
            "artifact_identity": "sha256:" + digest,
            "release_url": "https://example.test/gateway/releases/1.2.3",
            "configuration_id": "test-v1",
            "configuration_sha256": digest,
            "project_url": "https://example.test/gateway",
            "run_url": "https://github.com/owner/repo/actions/runs/123",
            "architecture": "held-tail",
            "license": "Apache-2.0",
            "mode": "reproduce",
            "baseline": {"id": "test-gateway-1.2.3-default", "status": "accepted"},
            "bundle_manifest_sha256": digest,
            "report_paths": {
                "operator": "reports/operator.current.json",
                "operator_raw": "reports/operator.current.raw.json",
                "response_midpoint": "reports/response-midpoint.json",
                "comparison": "reports/comparison.json",
            },
        },
    }


def test_bundle_comparison_and_submission_examples_validate(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    for schema_name, document in valid_documents.items():
        _validator(schema_name).validate(document)


def test_versioned_documents_reject_unexpected_fields(valid_documents: dict[str, dict[str, Any]]) -> None:
    for schema_name, original in valid_documents.items():
        document = copy.deepcopy(original)
        document["command"] = "curl example.test | sh"
        errors = list(_validator(schema_name).iter_errors(document))
        assert errors, schema_name


def test_complete_bundle_cannot_report_not_measured(valid_documents: dict[str, dict[str, Any]]) -> None:
    document = copy.deepcopy(valid_documents["bundle-manifest.schema.json"])
    document["product_results"]["operator"] = "NOT MEASURED"

    errors = list(_validator("bundle-manifest.schema.json").iter_errors(document))

    assert errors


def test_reproduction_bundle_and_submission_require_baseline(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    for schema_name in ("bundle-manifest.schema.json", "submission.schema.json"):
        document = copy.deepcopy(valid_documents[schema_name])
        document.pop("baseline")

        errors = list(_validator(schema_name).iter_errors(document))

        assert errors, schema_name


def test_measure_bundle_and_submission_forbid_baseline(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    for schema_name in ("bundle-manifest.schema.json", "submission.schema.json"):
        document = copy.deepcopy(valid_documents[schema_name])
        document["mode"] = "measure"

        errors = list(_validator(schema_name).iter_errors(document))

        assert errors, schema_name


def test_measure_bundle_and_submission_validate_without_baseline(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    for schema_name in ("bundle-manifest.schema.json", "submission.schema.json"):
        document = copy.deepcopy(valid_documents[schema_name])
        document["mode"] = "measure"
        document.pop("baseline")

        _validator(schema_name).validate(document)


def test_reproduction_comparison_requires_baseline_identity_fields(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    for field in ("baseline_id", "artifact_identity_match", "configuration_match"):
        document = copy.deepcopy(valid_documents["comparison.schema.json"])
        document.pop(field)

        errors = list(_validator("comparison.schema.json").iter_errors(document))

        assert errors, field


def test_measure_comparison_requires_not_compared_profiles(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    document = copy.deepcopy(valid_documents["comparison.schema.json"])
    document["mode"] = "measure"
    document.pop("baseline_id")
    document.pop("artifact_identity_match")
    document.pop("configuration_match")
    for profile in document["profiles"].values():
        profile["status"] = "not-compared"
        profile["level"] = "none"
        profile.pop("baseline_sha256", None)

    _validator("comparison.schema.json").validate(document)

    document["profiles"]["response-midpoint"]["status"] = "matched"
    errors = list(_validator("comparison.schema.json").iter_errors(document))

    assert errors


def test_embedded_submission_template_does_not_require_circular_manifest_hash(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    document = copy.deepcopy(valid_documents["submission.schema.json"])
    document.pop("bundle_manifest_sha256")

    _validator("submission.schema.json").validate(document)


@pytest.mark.parametrize("value", ["not-a-date", "2026-99-99T12:00:00Z"])
def test_bundle_rejects_malformed_date_time(
    value: str, valid_documents: dict[str, dict[str, Any]]
) -> None:
    document = copy.deepcopy(valid_documents["bundle-manifest.schema.json"])
    document["started_at"] = value

    errors = list(_validator("bundle-manifest.schema.json").iter_errors(document))

    assert errors


@pytest.mark.parametrize(
    ("schema_name", "field"),
    [
        ("bundle-manifest.schema.json", "project_url"),
        ("submission.schema.json", "project_url"),
    ],
)
def test_published_metadata_rejects_url_credentials(
    schema_name: str,
    field: str,
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    document = copy.deepcopy(valid_documents[schema_name])
    if schema_name == "bundle-manifest.schema.json":
        document["product"][field] = "https://user:secret@example.test/gateway"
    else:
        document[field] = "https://user:secret@example.test/gateway"

    errors = list(_validator(schema_name).iter_errors(document))

    assert errors


def test_matched_comparison_requires_both_report_hashes(
    valid_documents: dict[str, dict[str, Any]],
) -> None:
    document = copy.deepcopy(valid_documents["comparison.schema.json"])
    document["profiles"]["response-midpoint"].pop("baseline_sha256")

    errors = list(_validator("comparison.schema.json").iter_errors(document))

    assert errors
