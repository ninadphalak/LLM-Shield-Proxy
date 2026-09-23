from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acquisition import VerifiedWheel
from .paths import validate_fresh_output_path
from .release import _resolve_public_addresses

IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
RUN_SUFFIX = re.compile(r"^[a-f0-9]{16,32}$")
WHEEL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl$")
REQUIREMENT_PART = re.compile(r"^[A-Za-z0-9_.!+]+$")
MAX_WHEELHOUSE_BYTES = 512 * 1024 * 1024
BASE_IMAGE = (
    "python:3.12.11-slim-bookworm@"
    "sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
DOCKERFILE = Path(__file__).parent / "docker" / "llm-shield-proxy-released.Dockerfile"
EXPECTED_DOCKERFILE_SHA256 = "4187d888fdd6dd740a29a1d7304bb984e7c220212ab5bb3f7faaacab98360f31"
OWNER_LABEL = "org.pii-leak-benchmark.run-suffix"
WHEEL_LABEL = "org.pii-leak-benchmark.wheel-sha256"
LOCK_LABEL = "org.pii-leak-benchmark.dependency-lock-sha256"
DockerCommand = Callable[[list[str], float], subprocess.CompletedProcess[str]]
PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")


class DockerImageError(ValueError):
    """The run cannot prove that its Docker image came from the verified wheel."""


@dataclass(frozen=True)
class DependencyWheel:
    filename: str
    name: str
    version: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BuiltImage:
    image_id: str
    tag: str
    run_suffix: str
    wheel_sha256: str
    base_image: str
    distributions: tuple[tuple[str, str], ...]
    dependency_lock_sha256: str
    dependency_wheels: tuple[DependencyWheel, ...]


def _run_docker(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    if len(args) > 1 and args[1] == "build":
        return subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=timeout,
        )
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout,
    )


def _require_completed(
    docker: DockerCommand, args: list[str], timeout: float, *, operation: str
) -> subprocess.CompletedProcess[str]:
    try:
        completed = docker(args, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerImageError(f"Docker {operation} could not complete") from exc
    if completed.returncode != 0:
        raise DockerImageError(f"Docker {operation} failed")
    return completed


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size


def _inspect(docker: DockerCommand, reference: str) -> dict[str, Any]:
    result = _require_completed(
        docker,
        ["docker", "image", "inspect", "--format", "{{json .}}", reference],
        30,
        operation="image inspection",
    )
    try:
        document = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise DockerImageError("Docker image inspection returned invalid JSON") from exc
    if not isinstance(document, dict):
        raise DockerImageError("Docker image inspection returned invalid identity")
    return document


def _assert_owned(
    document: dict[str, Any], image_id: str, run_suffix: str,
    wheel_sha256: str, dependency_lock_sha256: str,
) -> None:
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if document.get("Id") != image_id or not isinstance(labels, dict):
        raise DockerImageError("Docker image identity does not match the build result")
    if (
        labels.get(OWNER_LABEL) != run_suffix
        or labels.get(WHEEL_LABEL) != wheel_sha256
        or labels.get(LOCK_LABEL) != dependency_lock_sha256
    ):
        raise DockerImageError("Docker image owner labels do not match this run")


def _dependency_wheels(wheelhouse: Path) -> tuple[DependencyWheel, ...]:
    found: list[DependencyWheel] = []
    names: set[str] = set()
    total_size = 0
    for path in sorted(wheelhouse.iterdir()):
        if not path.is_file() or path.is_symlink() or not WHEEL_NAME.fullmatch(path.name):
            raise DockerImageError("wheelhouse contains a non-wheel or unsafe entry")
        parts = path.name[:-4].split("-")
        if len(parts) not in (5, 6) or not REQUIREMENT_PART.fullmatch(parts[1]):
            raise DockerImageError("wheelhouse contains an invalid wheel filename")
        name = re.sub(r"[-_.]+", "-", parts[0]).lower()
        version = parts[1]
        if name in names:
            raise DockerImageError("wheelhouse contains duplicate distributions")
        names.add(name)
        try:
            entry_size = path.stat().st_size
        except OSError as exc:
            raise DockerImageError("wheelhouse entry cannot be inspected") from exc
        if not entry_size or entry_size > MAX_WHEELHOUSE_BYTES - total_size:
            raise DockerImageError("wheelhouse exceeds size limit")
        digest, size = _hash_file(path)
        total_size += size
        if not size or total_size > MAX_WHEELHOUSE_BYTES or len(found) >= 512:
            raise DockerImageError("wheelhouse exceeds size or member limit")
        found.append(DependencyWheel(path.name, name, version, digest, size))
    if not found:
        raise DockerImageError("wheelhouse is empty")
    return tuple(sorted(found, key=lambda item: item.name))


def _resolve_wheelhouse(
    docker: DockerCommand, context: Path, copied_wheel: Path,
) -> tuple[str, tuple[DependencyWheel, ...]]:
    wheelhouse = copied_wheel.parent
    pinned_hosts: list[str] = []
    for host in PYPI_HOSTS:
        addresses = _resolve_public_addresses(host, 443)
        ipv4 = next(
            (str(ipaddress.ip_address(value)) for value in addresses
             if ipaddress.ip_address(value).version == 4
             and ipaddress.ip_address(value).is_global),
            None,
        )
        if ipv4 is None:
            raise DockerImageError(f"official package host has no public IPv4 address: {host}")
        pinned_hosts.extend(("--add-host", f"{host}:{ipv4}"))
    _require_completed(
        docker,
        [
            "docker", "run", "--rm", "--mount", f"type=bind,source={context},target=/work",
            *pinned_hosts,
            BASE_IMAGE, "python", "-m", "pip", "--isolated", "download",
            "--index-url", "https://pypi.org/simple", "--disable-pip-version-check",
            "--only-binary=:all:", "--dest", "/work/wheelhouse",
            f"/work/wheelhouse/{copied_wheel.name}",
        ],
        600,
        operation="dependency wheel resolution",
    )
    wheels = _dependency_wheels(wheelhouse)
    locked = "".join(
        f"{item.name}=={item.version} --hash={item.sha256}\n" for item in wheels
    )
    (context / "requirements.lock").write_text(locked, encoding="utf-8", newline="\n")
    lock_hash = f"sha256:{hashlib.sha256(locked.encode('utf-8')).hexdigest()}"
    return lock_hash, wheels


def _inventory(docker: DockerCommand, image_id: str) -> tuple[tuple[str, str], ...]:
    result = _require_completed(
        docker,
        [
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
            image_id, "-m", "pip", "list", "--format=json",
        ],
        120,
        operation="distribution inventory",
    )
    if len(result.stdout) > 1_048_576:
        raise DockerImageError("Docker distribution inventory exceeds size limit")
    try:
        document = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise DockerImageError("Docker distribution inventory is invalid JSON") from exc
    if not isinstance(document, list) or len(document) > 512:
        raise DockerImageError("Docker distribution inventory is invalid")
    distributions: list[tuple[str, str]] = []
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("version"), str):
            raise DockerImageError("Docker distribution inventory contains an invalid entry")
        name, version = item["name"], item["version"]
        if not name or not version or len(name) > 128 or len(version) > 128:
            raise DockerImageError("Docker distribution inventory contains an invalid entry")
        distributions.append((re.sub(r"[-_.]+", "-", name).lower(), version))
    return tuple(sorted(distributions, key=lambda item: item[0].casefold()))


def build_release_image(
    wheel: VerifiedWheel,
    output_dir: Path,
    *,
    repo_root: Path,
    run_suffix: str,
    docker: DockerCommand = _run_docker,
) -> BuiltImage:
    """Build and inspect a run-owned image from one independently verified wheel."""
    if not RUN_SUFFIX.fullmatch(run_suffix):
        raise DockerImageError("run suffix is invalid")
    if not wheel.path.is_file() or wheel.path.is_symlink():
        raise DockerImageError("verified wheel path is not a regular file")
    observed_hash, observed_size = _hash_file(wheel.path)
    if observed_hash != wheel.sha256 or observed_size != wheel.size:
        raise DockerImageError("wheel digest or size changed before image build")
    try:
        dockerfile_bytes = DOCKERFILE.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    except OSError as exc:
        raise DockerImageError("reviewed Dockerfile is unavailable") from exc
    if hashlib.sha256(dockerfile_bytes).hexdigest() != EXPECTED_DOCKERFILE_SHA256:
        raise DockerImageError("reviewed Dockerfile content changed")
    destination = validate_fresh_output_path(output_dir, repo_root=repo_root)
    tag = f"pii-reproduction-{run_suffix}:released"
    try:
        existing = docker(["docker", "image", "inspect", "--format", "{{json .}}", tag], 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerImageError("Docker could not check the run-specific image tag") from exc
    if existing.returncode == 0:
        raise DockerImageError("run-specific Docker image tag already exists")
    if "no such image" not in (existing.stderr or "").lower():
        raise DockerImageError("Docker could not determine whether the run-specific image tag exists")
    destination.mkdir(parents=True, mode=0o700)
    context = destination / "context"
    context.mkdir()
    wheelhouse = context / "wheelhouse"
    wheelhouse.mkdir()
    copied_wheel = wheelhouse / wheel.path.name
    shutil.copyfile(wheel.path, copied_wheel)
    copied_hash, copied_size = _hash_file(copied_wheel)
    if copied_hash != wheel.sha256 or copied_size != wheel.size:
        raise DockerImageError("wheel digest or size changed while copying build context")
    lock_hash, dependency_wheels = _resolve_wheelhouse(docker, context, copied_wheel)
    if not any(item.sha256 == wheel.sha256 and item.filename == wheel.path.name for item in dependency_wheels):
        raise DockerImageError("wheelhouse no longer contains the verified released wheel")
    iidfile = destination / "image-id.txt"
    _require_completed(
        docker,
        [
            "docker", "build", "--pull", "--no-cache", "--network", "none", "--iidfile", str(iidfile),
            "--file", str(DOCKERFILE), "--tag", tag,
            "--label", f"{OWNER_LABEL}={run_suffix}",
            "--label", f"{WHEEL_LABEL}={wheel.sha256}",
            "--label", f"{LOCK_LABEL}={lock_hash}",
            str(context),
        ],
        900,
        operation="image build",
    )
    try:
        image_id = iidfile.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DockerImageError("Docker did not record a built image ID") from exc
    if not IMAGE_ID.fullmatch(image_id):
        raise DockerImageError("Docker returned an invalid image ID")
    _assert_owned(_inspect(docker, image_id), image_id, run_suffix, wheel.sha256, lock_hash)
    distributions = _inventory(docker, image_id)
    expected_version = wheel.path.name.split("-")[1]
    if ("llm-shield-proxy", expected_version) not in distributions:
        raise DockerImageError("installed distribution does not match the released wheel")
    if not {(item.name, item.version) for item in dependency_wheels}.issubset(distributions):
        raise DockerImageError("installed distributions do not match the dependency lock")
    return BuiltImage(
        image_id=image_id,
        tag=tag,
        run_suffix=run_suffix,
        wheel_sha256=wheel.sha256,
        base_image=BASE_IMAGE,
        distributions=distributions,
        dependency_lock_sha256=lock_hash,
        dependency_wheels=dependency_wheels,
    )


def remove_release_image(image: BuiltImage, *, docker: DockerCommand = _run_docker) -> None:
    """Remove only the immutable image ID carrying this run's ownership labels."""
    _assert_owned(
        _inspect(docker, image.image_id),
        image.image_id,
        image.run_suffix,
        image.wheel_sha256,
        image.dependency_lock_sha256,
    )
    _require_completed(
        docker, ["docker", "image", "rm", image.image_id], 120, operation="image removal"
    )
