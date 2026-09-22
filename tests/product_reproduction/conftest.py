from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def product_tree(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "repo"
    product_root = root / "benchmarks" / "product_reproduction"
    config_root = product_root / "configs"
    baseline_root = product_root / "baselines"
    config_root.mkdir(parents=True)
    baseline_root.mkdir()
    (config_root / "test-v1.json").write_text("{}\n", encoding="utf-8")
    (baseline_root / "response.json").write_text("{}\n", encoding="utf-8")
    return {
        "root": root,
        "product_root": product_root,
        "config_root": config_root,
        "baseline_root": baseline_root,
    }


@pytest.fixture
def valid_catalog(product_tree: dict[str, Path]) -> dict[str, Any]:
    return {
        "schema": "pii-leak-benchmark/product-catalog/v1",
        "release_sources": [
            {
                "id": "test-official",
                "project_id": "test-gateway",
                "project_url": "https://example.test/gateway",
                "source_type": "github-releases",
                "api_base_url": "https://api.github.com",
                "repository": "example/test-gateway",
                "version_pattern": r"^v?\d+\.\d+\.\d+$",
                "exclude": {
                    "prereleases": True,
                    "drafts": True,
                    "yanked": True,
                    "release_candidates": True,
                },
                "assets": {"name_pattern": r"^test-gateway-.*\.whl$", "media_types": ["application/zip"]},
                "pagination_limit": 5,
                "maximum_response_bytes": 1048576,
                "checksum_policy": "sha256-required",
                "eligible_adapters": ["test-gateway"],
                "eligible_configurations": ["test-v1"],
            }
        ],
        "targets": [
            {
                "id": "test-gateway-default",
                "project": "Test Gateway",
                "project_url": "https://example.test/gateway",
                "license": "Apache-2.0",
                "artifact_kind": "wheel",
                "adapter": "test-gateway",
                "release_source": "test-official",
                "configuration_id": "test-v1",
                "configuration_path": "benchmarks/product_reproduction/configs/test-v1.json",
                "architecture": "held-tail",
                "duty": "restore",
                "model": "capture",
                "profiles": ["operator", "response-midpoint"],
                "dispatchable": True,
                "runner": {
                    "class": "standard-ubuntu",
                    "minimum_memory_mib": 4096,
                    "minimum_disk_mib": 8192,
                    "service_limits": [{"service": "gateway", "memory_mib": 1024}],
                },
                "accepted_baselines": [
                    {
                        "id": "test-gateway-1.2.3-default",
                        "released_version": "1.2.3",
                        "artifact_reference": "test-gateway==1.2.3",
                        "artifact_identity": "sha256:" + "a" * 64,
                        "reports": {
                            "operator": None,
                            "response-midpoint": "benchmarks/product_reproduction/baselines/response.json",
                        },
                        "primary_outcomes": ["/summary/cases", "/summary/leaks"],
                        "comparison_policy": "product-reproduction/v1",
                        "accepted_by_pr": "https://github.com/example/test-gateway/pull/7",
                        "canary": {"enabled": True, "interval_days": 7},
                    }
                ],
            }
        ],
    }


@pytest.fixture
def catalog_file(product_tree: dict[str, Path], valid_catalog: dict[str, Any]) -> Path:
    path = product_tree["product_root"] / "catalog.json"
    path.write_text(json.dumps(valid_catalog), encoding="utf-8")
    return path
