from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from benchmarks.product_reproduction import bundle as bundle_module
from benchmarks.product_reproduction.bundle import (
    BundleContent,
    BundleError,
    IncompleteEvidenceError,
    build_bundle,
    verify_bundle,
)
from benchmarks.product_reproduction.catalog import RunnerRequirements, ServiceLimit
from benchmarks.product_reproduction.comparison import canonical_json_bytes, sha256_bytes
from benchmarks.product_reproduction.lifecycle import ProductLifecycleRunner
from benchmarks.product_reproduction.resources import ResourceSnapshot
from benchmarks.product_reproduction.results import ExperimentHealth
from benchmarks.product_reproduction.sanitizer import SensitiveValue
from tests.product_reproduction.fake_adapter import FakeBehavior, FakeGatewayAdapter, FakeState

DIGEST = "a" * 64
REVISION = "b" * 40
SENSITIVE = (SensitiveValue(label="FIXTURE_EMAIL", value="alice.fixture@example.test"),)
CONTROL_REPORT = {"control": "observed"}
OPERATOR_REPORT = {"verdict": "LEAK"}
OPERATOR_RAW_REPORT = {"result": "measured"}
RESPONSE_REPORT = {"outcome": "pass", "cases_inconclusive": 0}


def _comparison() -> dict:
    return {
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
                "current_sha256": sha256_bytes(canonical_json_bytes(OPERATOR_REPORT)),
                "differences": [],
            },
            "response-midpoint": {
                "status": "matched",
                "level": "exact",
                "current_sha256": sha256_bytes(canonical_json_bytes(RESPONSE_REPORT)),
                "baseline_sha256": DIGEST,
                "differences": [],
            },
        },
    }


def _submission() -> dict:
    return {
        "schema": "pii-leak-benchmark/submission/v2",
        "gateway": "Test Gateway",
        "requested_release_selector": "test-gateway-1.2.3-default",
        "version": "1.2.3",
        "artifact_identity": "sha256:" + DIGEST,
        "release_url": "https://example.test/gateway/releases/1.2.3",
        "configuration_id": "test-v1",
        "configuration_sha256": DIGEST,
        "project_url": "https://example.test/gateway",
        "run_url": "https://github.com/owner/repo/actions/runs/123",
        "architecture": "held-tail",
        "license": "Apache-2.0",
        "mode": "reproduce",
        "baseline": {"id": "test-gateway-1.2.3-default", "status": "accepted"},
        "report_paths": {
            "operator": "reports/operator.current.json",
            "operator_raw": "reports/operator.current.raw.json",
            "response_midpoint": "reports/response-midpoint.json",
            "comparison": "reports/comparison.json",
        },
    }


def _manifest() -> dict:
    return {
        "schema": "pii-leak-benchmark/product-bundle/v1",
        "schema_version": "1.0.0",
        "target_id": "test-gateway-default",
        "mode": "reproduce",
        "suite": ["operator", "response-midpoint"],
        "started_at": "2026-09-21T12:00:00Z",
        "ended_at": "2026-09-21T12:01:00Z",
        "experiment_health": "complete",
        "product_results": {"operator": "LEAK", "response_midpoint": "CLEAN"},
        "reproduction_status": {"operator": "not-compared", "response_midpoint": "matched"},
        "workflow": {
            "repository": "owner/repo",
            "workflow_ref": "owner/repo/.github/workflows/product-reproduction.yml@refs/heads/main",
            "run_id": "123",
            "run_attempt": 1,
            "actor": "operator",
            "commit_sha": REVISION,
            "branch": "main",
            "runner_image": "ubuntu-24.04",
            "runner_os": "Linux",
        },
        "harness": {
            "version": "0.1.0",
            "source_revision": REVISION,
            "instrument_digest": DIGEST,
            "install_artifact_sha256": DIGEST,
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
            "artifact_identity": "sha256:" + DIGEST,
            "release_url": "https://example.test/gateway/releases/1.2.3",
            "configuration_id": "test-v1",
            "configuration_sha256": DIGEST,
        },
        "profiles": {"seed": "a1b2c3d4e5f60001", "oracle": "midpoint", "iterations": 1},
        "resources": {
            "declared": {
                "runner_class": "standard-ubuntu",
                "minimum_memory_mib": 4096,
                "minimum_disk_mib": 8192,
                "service_limits": [{"service": "gateway", "memory_mib": 1024}],
            },
            "observed": {"available_memory_mib": 7000, "available_disk_mib": 20000},
        },
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
                    "sha256": DIGEST,
                },
            },
        },
        "limitations": ["One midpoint split per fixture."],
    }


def _members() -> list[BundleContent]:
    json_members = {
        "reports/control.raw.json": CONTROL_REPORT,
        "reports/operator.current.json": OPERATOR_REPORT,
        "reports/operator.current.raw.json": OPERATOR_RAW_REPORT,
        "reports/response-midpoint.json": RESPONSE_REPORT,
        "reports/comparison.json": _comparison(),
        "badge/pii-leak-badge.json": {"schemaVersion": 1, "label": "privacy", "message": "leak"},
        "submission/submission.json": _submission(),
        "provenance/runner.json": {"os": "Linux"},
        "provenance/target-artifacts.json": {"identity": "sha256:" + DIGEST},
        "provenance/environment.json": {"python": "3.12"},
        "provenance/workflow.json": {"run_id": "123"},
        "configuration/environment-allowlist.json": {"TARGET_API_KEY": "<TARGET_API_KEY>"},
    }
    text_members = {
        "README.md": "Fake adapter canonical evidence.\r\n",
        "submission/submission.md": "Prepared submission.\n",
        "submission/submission-url.txt": "https://example.test/submit\n",
        "provenance/commands.txt": "display: gateway --api-key <TARGET_API_KEY>\n",
        "provenance/dependency-inventory.txt": "fake-gateway==1.2.3\n",
        "configuration/rendered-config.json.txt": '{"upstream":"capture"}\r\n',
        "logs/orchestration.log": "lifecycle complete\n",
        "logs/readiness.log": "functional probe passed\n",
    }
    return [
        *(BundleContent.json(path, document) for path, document in json_members.items()),
        *(BundleContent.text(path, text) for path, text in text_members.items()),
    ]


def _build(tmp_path: Path, *, members: list[BundleContent] | None = None, manifest: dict | None = None):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    return build_bundle(
        repo_root=repo,
        bundle_dir=tmp_path / "artifact" / "bundle",
        archive_path=tmp_path / "artifact" / "product-reproduction-test-gateway-default-123.zip",
        manifest_template=manifest or _manifest(),
        members=members or _members(),
        sensitive_values=SENSITIVE,
    )


def test_build_and_verify_canonical_bundle_after_zip_round_trip(tmp_path: Path) -> None:
    result = _build(tmp_path)

    assert result.archive_path.is_file()
    assert result.bundle_dir.is_dir()
    assert result.verified.manifest["experiment_health"] == "complete"
    assert result.verified.manifest["product_results"]["operator"] == "LEAK"
    assert set(result.verified.member_sha256) == {
        item["path"] for item in result.verified.manifest["members"]
    }
    assert verify_bundle(result.bundle_dir, sensitive_values=SENSITIVE).manifest == result.verified.manifest


def test_verification_requires_sensitive_value_registry(tmp_path: Path) -> None:
    result = _build(tmp_path)

    with pytest.raises(BundleError, match="nonempty sensitive-value registry"):
        verify_bundle(result.archive_path, sensitive_values=())


def test_fake_adapter_lifecycle_produces_complete_leaking_canonical_bundle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = FakeState()
    behavior = FakeBehavior(operator_exit=1, response_exit=0)
    lifecycle = ProductLifecycleRunner(
        adapter_factory=lambda: FakeGatewayAdapter(state, behavior),
        resource_probe=lambda _: ResourceSnapshot(8192, 20000, None, True),
    ).run(
        target_id="test-gateway-default",
        runner_requirements=RunnerRequirements(
            runner_class="standard-ubuntu",
            minimum_memory_mib=4096,
            minimum_disk_mib=8192,
            service_limits=(ServiceLimit(service="gateway", memory_mib=1024),),
        ),
        output_dir=tmp_path / "lifecycle-output",
        repo_root=repo,
        sensitive_values=SENSITIVE,
    )
    manifest = _manifest()
    manifest["experiment_health"] = lifecycle.health.value
    manifest["product_results"] = {"operator": "LEAK", "response_midpoint": "CLEAN"}

    result = build_bundle(
        repo_root=repo,
        bundle_dir=tmp_path / "artifact" / "bundle",
        archive_path=tmp_path / "artifact" / "product-reproduction-test-gateway-default-123.zip",
        manifest_template=manifest,
        members=_members(),
        sensitive_values=SENSITIVE,
    )

    assert lifecycle.health is ExperimentHealth.COMPLETE
    assert lifecycle.exit_codes == {"operator": 1, "response-midpoint": 0}
    assert state.operator_calls == state.response_calls == 1
    assert result.verified.manifest["product_results"] == {
        "operator": "LEAK",
        "response_midpoint": "CLEAN",
    }


def test_bundle_bytes_are_deterministic_across_fresh_directories(tmp_path: Path) -> None:
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "two")

    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    assert first.archive_sha256 == second.archive_sha256


def test_changed_member_fails_manifest_and_checksum_validation(tmp_path: Path) -> None:
    result = _build(tmp_path)
    changed = result.bundle_dir / "reports" / "operator.current.json"
    changed.write_text('{"verdict":"CLEAN"}\n', encoding="utf-8")

    with pytest.raises(BundleError, match="hash or size mismatch"):
        verify_bundle(result.bundle_dir, sensitive_values=SENSITIVE)


def test_missing_required_report_is_not_measured(tmp_path: Path) -> None:
    members = [item for item in _members() if item.path != "reports/control.raw.json"]

    with pytest.raises(IncompleteEvidenceError) as caught:
        _build(tmp_path, members=members)

    assert caught.value.health is ExperimentHealth.NOT_MEASURED


@pytest.mark.parametrize("unsafe", ["../escape.json", "/absolute.json", r"C:\escape.json", "a/../b.json"])
def test_unsafe_bundle_member_is_rejected(tmp_path: Path, unsafe: str) -> None:
    members = _members() + [BundleContent.text(unsafe, "unsafe")]

    with pytest.raises(BundleError, match="path"):
        _build(tmp_path, members=members)


def test_duplicate_normalized_zip_member_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "one")
        archive.writestr("readme.md", "two")

    with pytest.raises(BundleError, match="duplicate normalized"):
        verify_bundle(archive_path, sensitive_values=SENSITIVE)


def test_noncanonical_zip_metadata_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "noncanonical.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", "not canonical")

    with pytest.raises(BundleError, match="metadata is not canonical"):
        verify_bundle(archive_path, sensitive_values=SENSITIVE)


@pytest.mark.parametrize(
    "leaking_content",
    [
        "alice.fixture@example.test",
        "YWxpY2UuZml4dHVyZUBleGFtcGxlLnRlc3Q=",
    ],
)
def test_fixture_or_encoded_specimen_never_enters_artifact(tmp_path: Path, leaking_content: str) -> None:
    members = [
        BundleContent.text(item.path, leaking_content)
        if item.path == "logs/orchestration.log"
        else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match="sensitive fixture"):
        _build(tmp_path, members=members)


def test_raw_logs_and_sanitizer_dictionary_are_rejected(tmp_path: Path) -> None:
    for path in (
        "logs/target.raw.log",
        "logs/target.log",
        "logs/gateway.log",
        "provenance/sanitizer-fixture-dictionary.json",
        "provenance/sanitizer-data.json",
    ):
        with pytest.raises(BundleError):
            _build(tmp_path / path.replace("/", "-"), members=_members() + [BundleContent.text(path, "hidden")])


def test_embedded_submission_rejects_circular_manifest_digest(tmp_path: Path) -> None:
    submission = _submission()
    submission["bundle_manifest_sha256"] = DIGEST
    members = [
        BundleContent.json(item.path, submission)
        if item.path == "submission/submission.json"
        else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match="cannot contain the final manifest hash"):
        _build(tmp_path, members=members)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
def test_json_source_rejects_nonfinite_numeric_values(tmp_path: Path, number: str) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"value":' + number + "}", encoding="utf-8")

    with pytest.raises(BundleError, match="JSON member is invalid"):
        BundleContent.from_path("provenance/source.json", source)


def test_direct_text_member_cannot_bypass_line_ending_canonicalization(tmp_path: Path) -> None:
    members = [
        BundleContent(path=item.path, data=b"unsafe\r\n", kind="text")
        if item.path == "logs/orchestration.log"
        else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match="canonical line endings"):
        _build(tmp_path, members=members)


def test_submission_and_comparison_must_agree_with_manifest(tmp_path: Path) -> None:
    submission = _submission()
    submission["version"] = "9.9.9"
    members = [
        BundleContent.json(item.path, submission)
        if item.path == "submission/submission.json"
        else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match="submission metadata disagrees"):
        _build(tmp_path, members=members)


def test_submission_run_url_must_match_manifest_workflow(tmp_path: Path) -> None:
    submission = _submission()
    submission["run_url"] = "https://github.com/owner/repo/actions/runs/999"
    members = [
        BundleContent.json(item.path, submission)
        if item.path == "submission/submission.json"
        else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match="submission metadata disagrees with manifest: run_url"):
        _build(tmp_path, members=members)


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        ("reports/operator.current.json", {"verdict": "CLEAN"}, "operator product result"),
        (
            "reports/response-midpoint.json",
            {"outcome": "fail", "cases_inconclusive": 0},
            "response product result",
        ),
        (
            "reports/response-midpoint.json",
            {"cases_inconclusive": 0},
            "unrecognized outcome",
        ),
    ],
)
def test_manifest_product_result_must_match_embedded_report(
    tmp_path: Path, path: str, replacement: dict, message: str
) -> None:
    members = [
        BundleContent.json(item.path, replacement) if item.path == path else item
        for item in _members()
    ]

    with pytest.raises(BundleError, match=message):
        _build(tmp_path, members=members)


def test_zip_path_traversal_is_rejected_before_extraction(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.json", "{}")
    path = tmp_path / "unsafe.zip"
    path.write_bytes(buffer.getvalue())

    with pytest.raises(BundleError, match="path"):
        verify_bundle(path, sensitive_values=SENSITIVE)


@pytest.mark.parametrize("position", ["leading", "trailing"])
def test_zip_rejects_bytes_outside_the_canonical_structure(tmp_path: Path, position: str) -> None:
    result = _build(tmp_path / "source")
    archive = result.archive_path.read_bytes()
    hidden = b"alice.fixture@example.test"
    tampered = hidden + archive if position == "leading" else archive + hidden
    path = tmp_path / f"{position}.zip"
    path.write_bytes(tampered)

    with pytest.raises(BundleError, match="canonical ZIP structure"):
        verify_bundle(path, sensitive_values=SENSITIVE)


def test_zip_member_limit_is_checked_before_opening_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "too-many.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(4):
            archive.writestr(f"member-{index}.txt", "value")
    monkeypatch.setattr(bundle_module, "MAX_MEMBERS", 3)

    def unexpected_zip_open(*args, **kwargs):
        raise AssertionError("ZIP opened before the central-directory member cap was checked")

    monkeypatch.setattr(bundle_module.zipfile, "ZipFile", unexpected_zip_open)

    with pytest.raises(BundleError, match="too many members"):
        verify_bundle(path, sensitive_values=SENSITIVE)


def test_directory_member_size_is_rejected_by_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "oversized.txt").write_bytes(b"12345")
    monkeypatch.setattr(bundle_module, "MAX_MEMBER_BYTES", 4)

    with pytest.raises(BundleError, match="member exceeds size limit"):
        verify_bundle(source, sensitive_values=SENSITIVE)


def test_directory_member_growth_cannot_bypass_the_read_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    growing = source / "growing.txt"
    growing.write_bytes(b"12345")
    original_stat = Path.stat

    class StaleStat:
        st_size = 1

    def stale_stat(path: Path, *args, **kwargs):
        if path == growing:
            return StaleStat()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stale_stat)
    monkeypatch.setattr(bundle_module, "MAX_MEMBER_BYTES", 4)

    with pytest.raises(BundleError, match="member exceeds size limit"):
        verify_bundle(source, sensitive_values=SENSITIVE)


def test_directory_member_limit_is_enforced_during_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    for index in range(4):
        (source / f"member-{index}.txt").write_text("value", encoding="utf-8")
    monkeypatch.setattr(bundle_module, "MAX_MEMBERS", 3)

    def guarded_rglob(path: Path, pattern: str):
        assert path == source
        assert pattern == "*"
        yield from source.iterdir()
        raise AssertionError("directory traversal continued beyond the member limit")

    monkeypatch.setattr(Path, "rglob", guarded_rglob)

    with pytest.raises(BundleError, match="too many members"):
        verify_bundle(source, sensitive_values=SENSITIVE)
