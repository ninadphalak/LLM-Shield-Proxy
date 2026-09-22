from __future__ import annotations

import pytest

from benchmarks.product_reproduction.catalog import RunnerRequirements, ServiceLimit
from benchmarks.product_reproduction.resources import (
    ResourceInsufficient,
    ResourceSnapshot,
    detect_cgroup_memory_limit_mib,
    validate_resource_admission,
)

REQUIREMENTS = RunnerRequirements(
    runner_class="standard-ubuntu",
    minimum_memory_mib=4096,
    minimum_disk_mib=8192,
    service_limits=(ServiceLimit(service="gateway", memory_mib=1024),),
)


def test_resource_admission_accepts_sufficient_host() -> None:
    snapshot = ResourceSnapshot(
        available_memory_mib=7000,
        available_disk_mib=20000,
        container_memory_limit_mib=6144,
        docker_available=True,
    )

    validate_resource_admission(REQUIREMENTS, snapshot)


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (ResourceSnapshot(4000, 20000, None, True), "memory"),
        (ResourceSnapshot(7000, 8000, None, True), "disk"),
        (ResourceSnapshot(7000, 20000, 2048, True), "container memory limit"),
        (ResourceSnapshot(7000, 20000, None, False), "Docker"),
    ],
)
def test_resource_admission_fails_before_start(snapshot: ResourceSnapshot, reason: str) -> None:
    with pytest.raises(ResourceInsufficient, match=reason):
        validate_resource_admission(REQUIREMENTS, snapshot)


@pytest.mark.parametrize(
    ("relative_path", "value", "expected"),
    [
        ("memory.max", "max\n", None),
        ("memory.max", str(5 * 1024 * 1024 * 1024), 5120),
        ("memory/memory.limit_in_bytes", str(3 * 1024 * 1024 * 1024), 3072),
    ],
)
def test_cgroup_memory_limit_detection(tmp_path, relative_path: str, value: str, expected: int | None) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")

    assert detect_cgroup_memory_limit_mib(tmp_path) == expected
