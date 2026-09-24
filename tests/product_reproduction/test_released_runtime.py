from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.product_reproduction.image import BuiltImage
from benchmarks.product_reproduction.released_runtime import (
    ReleasedRuntimeError,
    assert_released_container_identity,
    start_released_container,
    stop_released_container,
)

IMAGE_ID = "sha256:" + "a" * 64
CONTAINER_ID = "b" * 64
RUN_SUFFIX = "0123456789abcdef"


def _image() -> BuiltImage:
    return BuiltImage(
        image_id=IMAGE_ID,
        tag=f"pii-reproduction-{RUN_SUFFIX}:released",
        run_suffix=RUN_SUFFIX,
        wheel_sha256="sha256:" + "c" * 64,
        base_image="python:3.12.11-slim-bookworm@sha256:" + "d" * 64,
        distributions=(("llm-shield-proxy", "1.6.6"),),
        dependency_lock_sha256="sha256:" + "e" * 64,
        dependency_wheels=(),
    )


class FakeDocker:
    def __init__(self, *, wrong_image: bool = False, wrong_port: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.wrong_image = wrong_image
        self.wrong_port = wrong_port
        self.exists = False

    def __call__(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.commands.append(args)
        if args[1:3] == ["container", "inspect"]:
            if not self.exists:
                return subprocess.CompletedProcess(args, 1, "", "No such container")
            document = {
                "Id": CONTAINER_ID,
                "Image": ("sha256:" + "f" * 64) if self.wrong_image else IMAGE_ID,
                "Config": {"Labels": {"org.pii-leak-benchmark.run-suffix": RUN_SUFFIX}},
                "State": {"Running": True, "OOMKilled": False, "ExitCode": 0},
                "NetworkSettings": {"Ports": {
                    "8000/tcp": [{"HostIp": "0.0.0.0" if self.wrong_port else "127.0.0.1", "HostPort": "32789"}]
                }},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(document), "")
        if args[1] == "run":
            self.exists = True
            return subprocess.CompletedProcess(args, 0, CONTAINER_ID + "\n", "")
        if args[1:3] == ["container", "stop"]:
            return subprocess.CompletedProcess(args, 0, CONTAINER_ID, "")
        if args[1:3] == ["container", "rm"]:
            self.exists = False
            return subprocess.CompletedProcess(args, 0, CONTAINER_ID, "")
        raise AssertionError(args)


def test_runtime_uses_immutable_image_and_dynamic_loopback_port(tmp_path: Path) -> None:
    docker = FakeDocker()
    runtime = start_released_container(
        _image(),
        environment={
            "UPSTREAM_BASE_URL": "http://host.docker.internal:32788",
            "UPSTREAM_API_KEY": "synthetic-upstream-key",
        },
        working_dir=tmp_path,
        docker=docker,
    )
    assert runtime.container_id == CONTAINER_ID
    assert runtime.base_url == "http://127.0.0.1:32789"
    run = next(command for command in docker.commands if command[1] == "run")
    assert run[-1] == IMAGE_ID
    assert run[run.index("--publish") + 1] == "127.0.0.1::8000"
    assert "host.docker.internal:host-gateway" in run
    assert "synthetic-upstream-key" not in " ".join(run)
    assert (tmp_path / "container.env").read_text(encoding="utf-8").splitlines() == [
        "UPSTREAM_API_KEY=synthetic-upstream-key",
        "UPSTREAM_BASE_URL=http://host.docker.internal:32788",
    ]
    assert_released_container_identity(runtime, docker=docker)
    stop_released_container(runtime, docker=docker)
    assert [command[1:3] for command in docker.commands[-2:]] == [
        ["container", "stop"], ["container", "rm"]
    ]


@pytest.mark.parametrize("wrong_image,wrong_port", [(True, False), (False, True)])
def test_runtime_rejects_wrong_identity_and_cleans_its_own_container(
    tmp_path: Path, wrong_image: bool, wrong_port: bool,
) -> None:
    docker = FakeDocker(wrong_image=wrong_image, wrong_port=wrong_port)
    with pytest.raises(ReleasedRuntimeError):
        start_released_container(_image(), environment={}, working_dir=tmp_path, docker=docker)
    assert [command[1:3] for command in docker.commands[-2:]] == [
        ["container", "stop"], ["container", "rm"]
    ]


def test_runtime_rejects_env_injection_before_start(tmp_path: Path) -> None:
    docker = FakeDocker()
    with pytest.raises(ReleasedRuntimeError):
        start_released_container(
            _image(), environment={"UPSTREAM_BASE_URL": "http://safe\nEVIL=1"},
            working_dir=tmp_path, docker=docker,
        )
    assert not docker.commands


def test_runtime_rechecks_identity_before_scoring(tmp_path: Path) -> None:
    docker = FakeDocker()
    runtime = start_released_container(_image(), environment={}, working_dir=tmp_path, docker=docker)
    docker.wrong_image = True
    with pytest.raises(ReleasedRuntimeError, match="immutable image"):
        assert_released_container_identity(runtime, docker=docker)
    stop_released_container(runtime, docker=docker)
