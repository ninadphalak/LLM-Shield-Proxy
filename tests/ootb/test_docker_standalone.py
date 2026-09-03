"""The published image starts and serves health with no configuration beyond secrets.

Previously this rebuilt the image itself, bound a fixed host port, and ran
nowhere: CI passed `--ignore=tests/ootb`. It now shares the session-scoped build
with the other container tests, takes an ephemeral port so parallel runs cannot
collide, and asserts the readiness contract rather than liveness alone.
"""

from __future__ import annotations

import json
import urllib.request

from tests.ootb.docker_helpers import container_logs, free_port, wait_for_http

CONTAINER_NAME = "llm-shield-standalone-test"


def test_docker_standalone_happy_path(shield_image, run_container):
    host_port = free_port()
    run_container(
        CONTAINER_NAME,
        [
            "-p",
            f"127.0.0.1:{host_port}:8000",
            "-e",
            "SHIELD_ENCRYPTION_KEY=" + "00" * 32,
            "-e",
            "SHIELD_WATERMARK_SECRET=test-watermark",
            shield_image,
        ],
    )

    base_url = f"http://127.0.0.1:{host_port}"
    try:
        wait_for_http(f"{base_url}/healthz")
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n--- container logs ---\n{container_logs(CONTAINER_NAME)}") from exc

    with urllib.request.urlopen(f"{base_url}/healthz", timeout=5) as response:
        assert response.getcode() == 200
        assert json.loads(response.read())["status"] == "ok"

    # Readiness is the probe Kubernetes gates traffic on, and it runs the real
    # component checks (PII engine, vault, redis) rather than returning a
    # constant -- so a container that starts but cannot serve is caught here.
    with urllib.request.urlopen(f"{base_url}/readyz", timeout=10) as response:
        assert response.getcode() == 200, container_logs(CONTAINER_NAME)
        readiness = json.loads(response.read())

    assert readiness["status"] == "ready", readiness
    assert readiness["components"]["pii_engine"] == "ok"


def test_container_runs_as_a_non_root_user(shield_image):
    """The Dockerfile's `USER 10001` is a security control worth asserting."""
    import subprocess

    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", shield_image, "-c", "import os; print(os.getuid())"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "10001", f"container is running as uid {result.stdout.strip()}"
