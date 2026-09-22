from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from benchmarks.product_reproduction.comparison import (
    BaselineChangedError,
    ComparisonError,
    ComparisonPolicy,
    ReportSnapshot,
    compare_profile,
    comparison_document,
)
from benchmarks.product_reproduction.schemas import schema_validator


def _report(path: Path, document: dict) -> ReportSnapshot:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return ReportSnapshot.read(path)


def test_exact_comparison_ignores_only_enumerated_paths(tmp_path: Path) -> None:
    baseline = _report(tmp_path / "baseline.json", {"outcome": 3, "metadata": {"time": "old", "host": "a"}})
    current = _report(tmp_path / "current.json", {"outcome": 3, "metadata": {"time": "new", "host": "a"}})

    result = compare_profile(
        current,
        baseline,
        policy=ComparisonPolicy(frozenset({"/metadata/time"}), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )

    assert result.status == "matched"
    assert result.level == "exact"
    assert result.differences == ()


def test_non_primary_difference_is_result_reproduced_with_published_path(tmp_path: Path) -> None:
    baseline = _report(tmp_path / "baseline.json", {"outcome": 3, "metadata": {"runner": "old"}})
    current = _report(tmp_path / "current.json", {"outcome": 3, "metadata": {"runner": "new"}, "added": True})

    result = compare_profile(
        current,
        baseline,
        policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )

    assert result.status == "matched"
    assert result.level == "primary"
    assert result.differences == ("/added", "/metadata/runner")


def test_primary_or_subject_difference_is_drift(tmp_path: Path) -> None:
    baseline = _report(tmp_path / "baseline.json", {"summary": {"leaks": 0}, "inventory": ["a", "b"]})
    current = _report(tmp_path / "current.json", {"summary": {"leaks": 1}, "inventory": ["a"]})
    policy = ComparisonPolicy(frozenset(), frozenset({"/summary", "/inventory"}))

    report_drift = compare_profile(
        current,
        baseline,
        policy=policy,
        identity_matches=True,
        configuration_matches=True,
    )
    subject_drift = compare_profile(
        baseline,
        baseline,
        policy=policy,
        identity_matches=False,
        configuration_matches=True,
    )

    assert report_drift.status == "drifted"
    assert report_drift.differences == ("/inventory/1", "/summary/leaks")
    assert subject_drift.status == "drifted"
    assert subject_drift.level == "primary"


def test_missing_primary_path_refuses_comparison(tmp_path: Path) -> None:
    baseline = _report(tmp_path / "baseline.json", {"outcome": 3})
    current = _report(tmp_path / "current.json", {"outcome": 3})

    with pytest.raises(ComparisonError, match="primary comparison path"):
        compare_profile(
            current,
            baseline,
            policy=ComparisonPolicy(frozenset(), frozenset({"/missing"})),
            identity_matches=True,
            configuration_matches=True,
        )


def test_comparison_policy_requires_a_primary_outcome() -> None:
    with pytest.raises(ComparisonError, match="at least one primary"):
        ComparisonPolicy(frozenset(), frozenset())


def test_comparison_policy_rejects_ignored_primary_subtree() -> None:
    with pytest.raises(ComparisonError, match="both ignored and primary"):
        ComparisonPolicy(frozenset({"/summary/time"}), frozenset({"/summary"}))


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
def test_report_snapshot_rejects_nonfinite_json_numbers(tmp_path: Path, number: str) -> None:
    path = tmp_path / "report.json"
    path.write_text('{"outcome":' + number + "}", encoding="utf-8")

    with pytest.raises(ComparisonError, match="valid UTF-8 JSON"):
        ReportSnapshot.read(path)


def test_snapshot_detects_symlink_retargeting(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    link = tmp_path / "baseline.json"
    first.write_text('{"outcome":3}', encoding="utf-8")
    second.write_text('{"outcome":4}', encoding="utf-8")
    try:
        link.symlink_to(first)
    except OSError:
        pytest.skip("symbolic links are unavailable on this host")
    snapshot = ReportSnapshot.read(link)
    link.unlink()
    link.symlink_to(second)

    with pytest.raises(BaselineChangedError, match="changed during"):
        snapshot.assert_source_unchanged()


def test_snapshotted_baseline_refuses_mid_run_mutation(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    baseline = _report(path, {"outcome": 3})
    current = _report(tmp_path / "current.json", {"outcome": 3})
    path.write_text('{"outcome":4}', encoding="utf-8")

    with pytest.raises(BaselineChangedError, match="changed during"):
        compare_profile(
            current,
            baseline,
            policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
            identity_matches=True,
            configuration_matches=True,
        )


def test_comparison_document_validates_versioned_schema(tmp_path: Path) -> None:
    baseline = _report(tmp_path / "baseline.json", {"outcome": 3})
    current = _report(tmp_path / "current.json", {"outcome": 3})
    matched = compare_profile(
        current,
        baseline,
        policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )
    not_compared = compare_profile(
        current,
        None,
        policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )

    document = comparison_document(
        target_id="test-gateway-default",
        mode="reproduce",
        baseline_id="test-gateway-1.2.3-default",
        artifact_identity_match=True,
        configuration_match=True,
        operator=not_compared,
        response_midpoint=matched,
    )

    schema_validator("comparison").validate(document)


def test_measure_comparison_does_not_invent_baseline_identity(tmp_path: Path) -> None:
    current = _report(tmp_path / "current.json", {"outcome": 3})
    not_compared = compare_profile(
        current,
        None,
        policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )

    document = comparison_document(
        target_id="test-gateway-default",
        mode="measure",
        operator=not_compared,
        response_midpoint=not_compared,
    )

    assert "baseline_id" not in document
    assert "artifact_identity_match" not in document
    schema_validator("comparison").validate(document)


def test_measure_comparison_rejects_reproduction_status(tmp_path: Path) -> None:
    current = _report(tmp_path / "current.json", {"outcome": 3})
    baseline = _report(tmp_path / "baseline.json", {"outcome": 3})
    matched = compare_profile(
        current,
        baseline,
        policy=ComparisonPolicy(frozenset(), frozenset({"/outcome"})),
        identity_matches=True,
        configuration_matches=True,
    )

    with pytest.raises(ValidationError):
        comparison_document(
            target_id="test-gateway-default",
            mode="measure",
            operator=matched,
            response_midpoint=matched,
        )
