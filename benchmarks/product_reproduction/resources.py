from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .catalog import RunnerRequirements

MIB = 1024 * 1024


class ResourceInsufficient(RuntimeError):
    """The selected runner cannot safely admit the cataloged target."""


@dataclass(frozen=True)
class ResourceSnapshot:
    available_memory_mib: int
    available_disk_mib: int
    container_memory_limit_mib: int | None = None
    docker_available: bool = True

    def __post_init__(self) -> None:
        values = (self.available_memory_mib, self.available_disk_mib)
        if any(value < 0 for value in values):
            raise ValueError("resource observations cannot be negative")
        if self.container_memory_limit_mib is not None and self.container_memory_limit_mib < 0:
            raise ValueError("container memory limit cannot be negative")


def detect_cgroup_memory_limit_mib(cgroup_root: Path = Path("/sys/fs/cgroup")) -> int | None:
    candidates = (
        cgroup_root / "memory.max",
        cgroup_root / "memory" / "memory.limit_in_bytes",
    )
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if not value or value == "max":
            return None
        try:
            limit_bytes = int(value)
        except ValueError:
            continue
        if limit_bytes <= 0:
            continue
        return limit_bytes // MIB
    return None


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def collect_resource_snapshot(
    path: Path,
    *,
    docker_probe: Callable[[], bool] = _docker_available,
) -> ResourceSnapshot:
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - CI installs the declared development dependency.
        raise RuntimeError("psutil is required for product resource admission") from exc

    disk = shutil.disk_usage(path)
    memory = psutil.virtual_memory()
    return ResourceSnapshot(
        available_memory_mib=int(memory.available // MIB),
        available_disk_mib=int(disk.free // MIB),
        container_memory_limit_mib=detect_cgroup_memory_limit_mib(),
        docker_available=docker_probe(),
    )


def validate_resource_admission(requirements: RunnerRequirements, snapshot: ResourceSnapshot) -> None:
    failures: list[str] = []
    if not snapshot.docker_available:
        failures.append("Docker is unavailable")
    if snapshot.available_memory_mib < requirements.minimum_memory_mib:
        failures.append(
            f"available memory {snapshot.available_memory_mib} MiB is below {requirements.minimum_memory_mib} MiB"
        )
    if snapshot.available_disk_mib < requirements.minimum_disk_mib:
        failures.append(f"available disk {snapshot.available_disk_mib} MiB is below {requirements.minimum_disk_mib} MiB")
    if (
        snapshot.container_memory_limit_mib is not None
        and snapshot.container_memory_limit_mib < requirements.minimum_memory_mib
    ):
        failures.append(
            "container memory limit "
            f"{snapshot.container_memory_limit_mib} MiB is below {requirements.minimum_memory_mib} MiB"
        )
    if failures:
        raise ResourceInsufficient("; ".join(failures))
