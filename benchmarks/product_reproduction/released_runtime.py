from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import RELEASED_ENVIRONMENT_FIELDS
from .image import OWNER_LABEL, BuiltImage

CONTAINER_ID = re.compile(r"^[a-f0-9]{64}$")
ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAX_INSPECT_BYTES = 1_048_576
DockerCommand = Callable[[list[str], float], subprocess.CompletedProcess[str]]


class ReleasedRuntimeError(RuntimeError):
    """A released-product process cannot be safely measured or cleaned up."""


@dataclass(frozen=True)
class RunningContainer:
    container_id: str
    image_id: str
    run_suffix: str
    host_port: int
    name: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.host_port}"


def _run_docker(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout,
    )


def _completed(docker: DockerCommand, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        result = docker(args, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleasedRuntimeError("Docker command could not complete") from exc
    if result.returncode != 0:
        raise ReleasedRuntimeError("Docker command failed")
    return result


def _inspect(docker: DockerCommand, container_id: str) -> dict[str, Any]:
    result = _completed(
        docker, ["docker", "container", "inspect", "--format", "{{json .}}", container_id], 30,
    )
    if not isinstance(result.stdout, str) or len(result.stdout) > MAX_INSPECT_BYTES:
        raise ReleasedRuntimeError("Docker container inspection is oversized")
    try:
        document = json.loads(result.stdout)
    except ValueError as exc:
        raise ReleasedRuntimeError("Docker container inspection is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ReleasedRuntimeError("Docker container inspection is invalid")
    return document


def _owned(document: dict[str, Any], container_id: str, run_suffix: str) -> bool:
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    return (
        document.get("Id") == container_id
        and isinstance(labels, dict)
        and labels.get(OWNER_LABEL) == run_suffix
    )


def _host_port(document: dict[str, Any]) -> int:
    network = document.get("NetworkSettings")
    ports = network.get("Ports") if isinstance(network, dict) else None
    binding = ports.get("8000/tcp") if isinstance(ports, dict) else None
    if not isinstance(binding, list) or len(binding) != 1 or not isinstance(binding[0], dict):
        raise ReleasedRuntimeError("Docker did not publish exactly one target port")
    if binding[0].get("HostIp") != "127.0.0.1":
        raise ReleasedRuntimeError("target port is not loopback-only")
    raw_port = binding[0].get("HostPort")
    if not isinstance(raw_port, str) or not raw_port.isdecimal():
        raise ReleasedRuntimeError("Docker assigned an invalid host port")
    port = int(raw_port)
    if not 0 < port < 65536:
        raise ReleasedRuntimeError("Docker assigned an invalid host port")
    return port


def _write_environment(environment: Mapping[str, str], working_dir: Path) -> Path:
    if not working_dir.is_dir() or working_dir.is_symlink():
        raise ReleasedRuntimeError("runtime working directory is unavailable")
    lines: list[str] = []
    for key, value in sorted(environment.items()):
        if not isinstance(key, str) or not ENVIRONMENT_KEY.fullmatch(key) or key not in RELEASED_ENVIRONMENT_FIELDS:
            raise ReleasedRuntimeError("runtime environment contains an unreviewed key")
        if (
            not isinstance(value, str) or not value or len(value) > 4096
            or any(character in value for character in "\r\n\x00")
        ):
            raise ReleasedRuntimeError("runtime environment contains an invalid value")
        lines.append(f"{key}={value}\n")
    path = working_dir / "container.env"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
    except OSError as exc:
        raise ReleasedRuntimeError("runtime environment file cannot be created") from exc
    return path


def _cleanup_owned(docker: DockerCommand, container_id: str, run_suffix: str) -> None:
    document = _inspect(docker, container_id)
    if not _owned(document, container_id, run_suffix):
        raise ReleasedRuntimeError("container ownership changed; cleanup refused")
    state = document.get("State")
    if isinstance(state, dict) and state.get("Running") is True:
        _completed(docker, ["docker", "container", "stop", "--time", "10", container_id], 30)
    _completed(docker, ["docker", "container", "rm", container_id], 30)


def start_released_container(
    image: BuiltImage,
    *,
    environment: Mapping[str, str],
    working_dir: Path,
    docker: DockerCommand = _run_docker,
) -> RunningContainer:
    """Start a run-owned immutable image on an OS-assigned loopback host port."""
    env_path = _write_environment(environment, working_dir)
    name = f"pii-reproduction-{image.run_suffix}"
    try:
        existing = docker(
            ["docker", "container", "inspect", "--format", "{{json .}}", name], 30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleasedRuntimeError("Docker could not check the run-specific container name") from exc
    if existing.returncode == 0:
        raise ReleasedRuntimeError("run-specific container name already exists")
    if "no such container" not in (existing.stderr or "").lower():
        raise ReleasedRuntimeError("Docker could not determine whether the container name exists")
    result = _completed(
        docker,
        [
            "docker", "run", "--detach", "--pull=never", "--name", name,
            "--label", f"{OWNER_LABEL}={image.run_suffix}",
            "--network", "bridge", "--add-host", "host.docker.internal:host-gateway",
            "--publish", "127.0.0.1::8000", "--env-file", str(env_path), image.image_id,
        ],
        60,
    )
    container_id = (result.stdout or "").strip()
    if not CONTAINER_ID.fullmatch(container_id):
        raise ReleasedRuntimeError("Docker returned an invalid container ID")
    try:
        document = _inspect(docker, container_id)
        if not _owned(document, container_id, image.run_suffix):
            raise ReleasedRuntimeError("container owner identity does not match this run")
        if document.get("Image") != image.image_id:
            raise ReleasedRuntimeError("container did not start from the immutable image ID")
        state = document.get("State")
        if not isinstance(state, dict) or state.get("Running") is not True or state.get("OOMKilled") is True:
            raise ReleasedRuntimeError("released product container is not running")
        port = _host_port(document)
    except ReleasedRuntimeError:
        try:
            _cleanup_owned(docker, container_id, image.run_suffix)
        except ReleasedRuntimeError:
            pass
        raise
    return RunningContainer(container_id, image.image_id, image.run_suffix, port, name)


def assert_released_container_identity(
    runtime: RunningContainer, *, docker: DockerCommand = _run_docker,
) -> None:
    """Recheck the exact image, owner, running state and published port before scoring."""
    document = _inspect(docker, runtime.container_id)
    if not _owned(document, runtime.container_id, runtime.run_suffix):
        raise ReleasedRuntimeError("container owner identity changed")
    if document.get("Image") != runtime.image_id:
        raise ReleasedRuntimeError("container immutable image identity changed")
    state = document.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise ReleasedRuntimeError("released product container is not running")
    if state.get("OOMKilled") is True:
        raise ReleasedRuntimeError("released product container was OOM-killed")
    if _host_port(document) != runtime.host_port:
        raise ReleasedRuntimeError("published target port identity changed")


def stop_released_container(runtime: RunningContainer, *, docker: DockerCommand = _run_docker) -> None:
    """Stop only the exact container ID carrying this run's owner label."""
    _cleanup_owned(docker, runtime.container_id, runtime.run_suffix)
