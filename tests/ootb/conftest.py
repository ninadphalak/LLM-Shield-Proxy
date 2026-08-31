"""Shared Docker fixtures for the out-of-the-box container tests.

These tests were excluded from CI (`--ignore=tests/ootb`) and skipped or errored
on developer machines, so the container contract -- that the published image
starts, serves health, and drains gracefully on SIGTERM -- had never actually
been executed anywhere. Docker is preinstalled on `ubuntu-latest`, so they now
run there.

`SHIELD_REQUIRE_DOCKER=1` (set by CI) turns the "Docker unavailable" skip into a
hard failure, so the suite cannot go green by quietly skipping the very tests it
was added to run.
"""

from __future__ import annotations

import subprocess
from typing import Iterator

import pytest

from tests.ootb.docker_helpers import IMAGE_TAG, REPO_ROOT, REQUIRE_DOCKER, docker_status


@pytest.fixture(scope="session")
def docker_required() -> None:
    available, reason = docker_status()
    if available:
        return
    if REQUIRE_DOCKER:
        pytest.fail(
            f"SHIELD_REQUIRE_DOCKER=1 but Docker is not usable ({reason}). "
            "Refusing to skip: these tests exist to exercise the real container."
        )
    pytest.skip(f"Docker is unavailable ({reason})")


@pytest.fixture(scope="session")
def shield_image(docker_required) -> str:
    """Build the production image once and share it across the session."""
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert build.returncode == 0, (
        f"docker build failed:\n{build.stdout[-4000:]}\n{build.stderr[-4000:]}"
    )
    return IMAGE_TAG


@pytest.fixture
def run_container() -> Iterator:
    """Start containers by name and guarantee they are removed afterwards."""
    started: list[str] = []

    def _run(name: str, args: list[str]) -> str:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        result = subprocess.run(
            ["docker", "run", "-d", "--name", name, *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"docker run failed: {result.stderr}"
        started.append(name)
        return result.stdout.strip()

    try:
        yield _run
    finally:
        for name in started:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
