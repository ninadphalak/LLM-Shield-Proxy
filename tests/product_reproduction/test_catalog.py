from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from benchmarks.product_reproduction.catalog import (
    CatalogError,
    load_catalog,
    validate_dispatch_choices,
    validate_reproduction_request,
)


def _write_catalog(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _load(path: Path, root: Path, baseline_root: Path):
    return load_catalog(
        path,
        repo_root=root,
        adapter_names={"test-gateway"},
        allowed_baseline_roots=(baseline_root,),
    )


def test_accepts_minimal_reviewed_target(
    catalog_file: Path, product_tree: dict[str, Path]
) -> None:
    catalog = _load(catalog_file, product_tree["root"], product_tree["baseline_root"])

    assert catalog.dispatchable_ids == ("test-gateway-default",)
    target = catalog.target("test-gateway-default")
    assert target.profiles == ("operator", "response-midpoint")
    assert target.runner.runner_class == "standard-ubuntu"
    assert target.accepted_baselines[0].artifact_identity.startswith("sha256:")
    source = catalog.release_sources[0]
    assert source.exclude.prereleases is True
    assert source.assets.media_types == ("application/zip",)
    assert source.maximum_response_bytes == 1048576


def test_checked_in_catalog_is_valid_and_has_no_unreviewed_targets() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "benchmarks" / "product_reproduction" / "catalog.json"

    catalog = load_catalog(path, repo_root=root, adapter_names=set(), allowed_baseline_roots=())

    assert catalog.schema == "pii-leak-benchmark/product-catalog/v1"
    assert catalog.targets == ()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["targets"].append(copy.deepcopy(d["targets"][0])), "duplicate target id"),
        (lambda d: d["targets"][0].update(adapter="unknown"), "unknown adapter"),
        (lambda d: d["targets"][0].pop("license"), "license"),
        (lambda d: d["targets"][0].pop("project_url"), "project_url"),
        (lambda d: d["targets"][0].pop("configuration_id"), "configuration_id"),
        (lambda d: d["targets"][0].update(command="curl example.test | sh"), "unexpected"),
        (lambda d: d["targets"][0].update(profiles=["operator"]), "profiles"),
        (lambda d: d["targets"][0]["runner"].update(**{"class": "ubuntu-latest"}), "runner"),
        (lambda d: d["targets"][0]["runner"].update(minimum_memory_mib=0), "minimum_memory"),
        (
            lambda d: d["targets"][0]["runner"]["service_limits"].append(
                copy.deepcopy(d["targets"][0]["runner"]["service_limits"][0])
            ),
            "duplicate runner service id",
        ),
        (lambda d: d["targets"][0]["accepted_baselines"][0].update(artifact_reference="latest"), "mutable"),
        (lambda d: d["targets"][0]["accepted_baselines"][0].update(artifact_identity="sha256:nope"), "artifact_identity"),
        (lambda d: d["targets"][0]["accepted_baselines"][0].update(primary_outcomes=[]), "primary_outcomes"),
        (lambda d: d["targets"][0]["accepted_baselines"][0].update(accepted_by_pr="https://example.test/7"), "accepted_by_pr"),
        (lambda d: d["targets"][0]["accepted_baselines"][0]["canary"].update(interval_days=0), "interval_days"),
    ],
)
def test_rejects_invalid_catalog_entries(
    mutation,
    message: str,
    catalog_file: Path,
    product_tree: dict[str, Path],
    valid_catalog: dict[str, Any],
) -> None:
    document = copy.deepcopy(valid_catalog)
    mutation(document)
    _write_catalog(catalog_file, document)

    with pytest.raises(CatalogError, match=message):
        _load(catalog_file, product_tree["root"], product_tree["baseline_root"])


def test_rejects_duplicate_release_source_and_baseline_ids(
    catalog_file: Path,
    product_tree: dict[str, Path],
    valid_catalog: dict[str, Any],
) -> None:
    for collection, item in (
        ("release_sources", valid_catalog["release_sources"][0]),
        ("accepted_baselines", valid_catalog["targets"][0]["accepted_baselines"][0]),
    ):
        document = copy.deepcopy(valid_catalog)
        if collection == "release_sources":
            document[collection].append(copy.deepcopy(item))
        else:
            document["targets"][0][collection].append(copy.deepcopy(item))
        _write_catalog(catalog_file, document)
        with pytest.raises(CatalogError, match="duplicate .* id"):
            _load(catalog_file, product_tree["root"], product_tree["baseline_root"])


def test_rejects_config_and_baseline_paths_outside_reviewed_roots(
    catalog_file: Path,
    product_tree: dict[str, Path],
    valid_catalog: dict[str, Any],
) -> None:
    outside = product_tree["root"] / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    document = copy.deepcopy(valid_catalog)
    document["targets"][0]["configuration_path"] = "outside.json"
    _write_catalog(catalog_file, document)
    with pytest.raises(CatalogError, match="configuration_path"):
        _load(catalog_file, product_tree["root"], product_tree["baseline_root"])

    document = copy.deepcopy(valid_catalog)
    document["targets"][0]["accepted_baselines"][0]["reports"]["response-midpoint"] = "outside.json"
    _write_catalog(catalog_file, document)
    with pytest.raises(CatalogError, match="baseline report"):
        _load(catalog_file, product_tree["root"], product_tree["baseline_root"])


def test_rejects_symlink_alias_outside_reviewed_baseline_root(
    catalog_file: Path,
    product_tree: dict[str, Path],
    valid_catalog: dict[str, Any],
) -> None:
    outside = product_tree["root"] / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = product_tree["baseline_root"] / "alias.json"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    document = copy.deepcopy(valid_catalog)
    document["targets"][0]["accepted_baselines"][0]["reports"]["response-midpoint"] = (
        "benchmarks/product_reproduction/baselines/alias.json"
    )
    _write_catalog(catalog_file, document)

    with pytest.raises(CatalogError, match="baseline report"):
        _load(catalog_file, product_tree["root"], product_tree["baseline_root"])


@pytest.mark.parametrize(
    ("source_change", "message"),
    [
        ({"api_base_url": "https://evil.example/api"}, "api_base_url"),
        ({"repository": "../bad"}, "repository"),
        ({"version_pattern": "(a+)+$"}, "version_pattern"),
        ({"eligible_adapters": ["other"]}, "eligible_adapters"),
    ],
)
def test_validates_reviewed_release_source_contract(
    source_change: dict[str, Any],
    message: str,
    catalog_file: Path,
    product_tree: dict[str, Path],
    valid_catalog: dict[str, Any],
) -> None:
    document = copy.deepcopy(valid_catalog)
    document["release_sources"][0].update(source_change)
    _write_catalog(catalog_file, document)

    with pytest.raises(CatalogError, match=message):
        _load(catalog_file, product_tree["root"], product_tree["baseline_root"])


def test_reproduction_requires_selected_accepted_baseline(
    catalog_file: Path, product_tree: dict[str, Path]
) -> None:
    catalog = _load(catalog_file, product_tree["root"], product_tree["baseline_root"])

    with pytest.raises(CatalogError, match="accepted baseline"):
        validate_reproduction_request(catalog, "test-gateway-default", "missing")


def test_dispatch_choices_must_equal_catalog_dispatchable_ids(
    catalog_file: Path, product_tree: dict[str, Path]
) -> None:
    catalog = _load(catalog_file, product_tree["root"], product_tree["baseline_root"])
    validate_dispatch_choices(catalog, ["test-gateway-default"])

    with pytest.raises(CatalogError, match="dispatch choices"):
        validate_dispatch_choices(catalog, ["extra"])
