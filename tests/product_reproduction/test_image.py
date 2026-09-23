from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.product_reproduction.acquisition import VerifiedWheel
from benchmarks.product_reproduction.image import (
    DOCKERFILE,
    DockerImageError,
    build_release_image,
    remove_release_image,
)

IMAGE_ID = "sha256:" + "c" * 64


def _wheel(tmp_path: Path) -> VerifiedWheel:
    path = tmp_path / "llm_shield_proxy-1.6.6-py3-none-any.whl"
    payload = b"verified wheel bytes"
    path.write_bytes(payload)
    return VerifiedWheel(
        path=path,
        sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        source_url="https://files.pythonhosted.org/packages/reviewed.whl",
    )


class FakeDocker:
    def __init__(self, *, mismatched_owner: bool = False, unsafe_dependency: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.mismatched_owner = mismatched_owner
        self.unsafe_dependency = unsafe_dependency
        self.labels: dict[str, str] = {}

    def __call__(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.commands.append(args)
        if args[1:3] == ["image", "inspect"]:
            if args[-1].startswith("pii-reproduction-") and not self.labels:
                return subprocess.CompletedProcess(args, 1, "", "not found")
            labels = dict(self.labels)
            if self.mismatched_owner:
                labels["org.pii-leak-benchmark.run-suffix"] = "different"
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps({"Id": IMAGE_ID, "Config": {"Labels": labels}}),
                "",
            )
        if args[1] == "build":
            for index, item in enumerate(args):
                if item == "--label":
                    key, value = args[index + 1].split("=", 1)
                    self.labels[key] = value
                if item == "--iidfile":
                    Path(args[index + 1]).write_text(IMAGE_ID + "\n", encoding="ascii")
            return subprocess.CompletedProcess(args, 0, "build complete", "")
        if args[1] == "run":
            if "download" in args:
                mount = args[args.index("--mount") + 1]
                source = mount.split("source=", 1)[1].split(",target=", 1)[0]
                extension = "tar.gz" if self.unsafe_dependency else "whl"
                (Path(source) / "wheelhouse" / f"dependency-1.0-py3-none-any.{extension}").write_bytes(
                    b"downloaded dependency bytes"
                )
                return subprocess.CompletedProcess(args, 0, "download complete", "")
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps([
                    {"name": "llm_shield_proxy", "version": "1.6.6"},
                    {"name": "Dependency", "version": "1.0"},
                ]),
                "",
            )
        if args[1:3] == ["image", "rm"]:
            return subprocess.CompletedProcess(args, 0, "removed", "")
        raise AssertionError(args)


def test_build_uses_verified_wheel_and_immutable_image_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wheel = _wheel(tmp_path)
    docker = FakeDocker()

    image = build_release_image(
        wheel,
        tmp_path / "outside" / "build",
        repo_root=repo,
        run_suffix="0123456789abcdef",
        docker=docker,
    )

    assert image.image_id == IMAGE_ID
    assert image.wheel_sha256 == wheel.sha256
    assert image.distributions == (("dependency", "1.0"), ("llm-shield-proxy", "1.6.6"))
    assert len(image.dependency_wheels) == 2
    assert image.dependency_lock_sha256.startswith("sha256:")
    build = next(command for command in docker.commands if command[1] == "build")
    assert "--pull" in build and "--no-cache" in build
    assert build[build.index("--network") + 1] == "none"
    assert wheel.path.name in [path.name for path in (Path(build[-1]) / "wheelhouse").iterdir()]
    lock = (Path(build[-1]) / "requirements.lock").read_text(encoding="utf-8")
    assert "llm-shield-proxy==1.6.6 --hash=sha256:" in lock
    assert "dependency==1.0 --hash=sha256:" in lock
    assert "--require-hashes" in DOCKERFILE.read_text(encoding="utf-8")
    resolver = next(command for command in docker.commands if command[1] == "run" and "download" in command)
    assert "--only-binary=:all:" in resolver
    inventory = next(command for command in docker.commands if command[1] == "run" and "list" in command)
    assert inventory[inventory.index("--network") + 1] == "none"
    assert IMAGE_ID in inventory
    remove_release_image(image, docker=docker)
    assert ["docker", "image", "rm", IMAGE_ID] in docker.commands


def test_changed_wheel_is_rejected_before_build(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wheel = _wheel(tmp_path)
    wheel.path.write_bytes(b"modified after verification")
    docker = FakeDocker()

    with pytest.raises(DockerImageError, match="wheel digest"):
        build_release_image(
            wheel,
            tmp_path / "outside" / "build",
            repo_root=repo,
            run_suffix="0123456789abcdef",
            docker=docker,
        )

    assert docker.commands == []


def test_image_identity_mismatch_blocks_inventory_and_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    docker = FakeDocker(mismatched_owner=True)

    with pytest.raises(DockerImageError, match="owner"):
        build_release_image(
            _wheel(tmp_path),
            tmp_path / "outside" / "build",
            repo_root=repo,
            run_suffix="0123456789abcdef",
            docker=docker,
        )

    assert not any(command[1] == "run" and "list" in command for command in docker.commands)
    assert not any(command[1:3] == ["image", "rm"] for command in docker.commands)


def test_non_wheel_dependency_blocks_image_build(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    docker = FakeDocker(unsafe_dependency=True)

    with pytest.raises(DockerImageError, match="wheelhouse"):
        build_release_image(
            _wheel(tmp_path),
            tmp_path / "outside" / "build",
            repo_root=repo,
            run_suffix="0123456789abcdef",
            docker=docker,
        )

    assert not any(command[1] == "build" for command in docker.commands)
