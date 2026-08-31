"""Plain helpers shared by the out-of-the-box container tests.

Kept out of ``conftest.py`` so the test modules can import them directly rather
than reaching into a conftest, which pytest owns.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "llm-shield:ootb-test"
REQUIRE_DOCKER = os.environ.get("SHIELD_REQUIRE_DOCKER") == "1"


def docker_status() -> tuple[bool, str]:
    """Report whether a usable Docker daemon is reachable, and why not if it is not."""
    if shutil.which("docker") is None:
        return False, "the docker CLI is not on PATH"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if probe.returncode != 0:
        return False, f"`docker info` failed: {probe.stderr.strip()[:300]}"
    return True, ""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout: float = 60.0) -> None:
    """Poll a URL until it answers 200, raising with the last error on timeout."""
    import urllib.request

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.getcode() == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - any failure means "not up yet"
            last = exc
        time.sleep(0.5)
    raise AssertionError(f"{url} never returned 200 within {timeout}s (last error: {last})")


def container_logs(name: str) -> str:
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "\n".join([result.stdout, result.stderr])
