"""SIGTERM graceful shutdown / pod drain, against the real container.

The only prior SIGTERM test (`tests/k8s/test_lifecycle.py`) required an
already-running container named `llm-shield-proxy`, was excluded from CI, and
swallowed every failure mode in bare `except` blocks -- so it could not have
failed even if the proxy had severed the connection or been SIGKILLed. The rest
of the suite only flipped `app_state.is_draining` by hand.

These tests start the production image, put a genuinely slow request in flight,
send a real SIGTERM to PID 1, and then assert the three things a Kubernetes pod
drain actually depends on:

1. the in-flight request completes correctly rather than being cut off;
2. the process exits of its own accord with status 0, not via SIGKILL;
3. the listener stops accepting new connections once the drain has begun.

A slow upstream runs on the host and is reached from the container through
`host.docker.internal:host-gateway`, which keeps the request open long enough
for the drain window to be observable rather than a race.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Iterator

import pytest

from tests.ootb.docker_helpers import container_logs, free_port, wait_for_http

UPSTREAM_DELAY_SECONDS = 8.0
CONTAINER_NAME = "llm-shield-drain-test"

# Echoed back by the slow upstream so the assertion covers rehydration, not just
# "some bytes arrived": the proxy masks the name on the way out and must restore
# it on the way back even while it is shutting down.
SECRET_EMAIL = "jane.doe@example.com"


class _SlowUpstream(http.server.BaseHTTPRequestHandler):
    """An OpenAI-shaped upstream that answers slowly, on purpose."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)

        time.sleep(UPSTREAM_DELAY_SECONDS)

        body = json.dumps(
            {
                "id": "chatcmpl-drain",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Understood, [EMAIL_1]."},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D102 - silence stderr spam
        return


@pytest.fixture
def slow_upstream() -> Iterator[int]:
    port = free_port()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), _SlowUpstream)  # noqa: S104
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def draining_proxy(shield_image, run_container, slow_upstream) -> Iterator[dict]:
    """A running container wired to the slow upstream on the host."""
    host_port = free_port()
    run_container(
        CONTAINER_NAME,
        [
            "-p",
            f"127.0.0.1:{host_port}:8000",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            "SHIELD_ENCRYPTION_KEY=" + "00" * 32,
            "-e",
            "SHIELD_WATERMARK_SECRET=drain-test",
            "-e",
            f"UPSTREAM_BASE_URL=http://host.docker.internal:{slow_upstream}",
            "-e",
            "ENABLE_OPEN_BYOK_PASSTHROUGH=true",
            "-e",
            "ENABLE_SYNTHETIC_SWAPPING=false",
            "-e",
            "ENABLE_CANARY_TRIPWIRE=false",
            "-e",
            "DRAIN_TIMEOUT_SECONDS=25",
            shield_image,
        ],
    )

    base_url = f"http://127.0.0.1:{host_port}"
    try:
        wait_for_http(f"{base_url}/healthz")
    except AssertionError as exc:  # surface the container's own diagnosis
        raise AssertionError(f"{exc}\n--- container logs ---\n{container_logs(CONTAINER_NAME)}") from exc

    yield {"base_url": base_url, "name": CONTAINER_NAME}


def _post_chat(base_url: str, sink: dict) -> None:
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": f"My email is {SECRET_EMAIL}."}],
            }
        ).encode(),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer sk-proj-drain-test-key",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=UPSTREAM_DELAY_SECONDS + 30) as response:
            sink["status"] = response.getcode()
            sink["body"] = response.read().decode()
    except Exception as exc:  # noqa: BLE001 - the failure itself is the result
        sink["error"] = f"{type(exc).__name__}: {exc}"


def _container_state(name: str) -> dict:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}", name],
        capture_output=True,
        text=True,
        check=True,
    )
    running, exit_code, oom = result.stdout.split()
    return {"running": running == "true", "exit_code": int(exit_code), "oom_killed": oom == "true"}


def _wait_for_exit(name: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _container_state(name)
        if not state["running"]:
            return state
        time.sleep(0.25)
    raise AssertionError(
        f"container {name} was still running {timeout}s after SIGTERM; "
        f"logs:\n{container_logs(name)}"
    )


def test_sigterm_completes_in_flight_requests_and_exits_cleanly(draining_proxy):
    base_url = draining_proxy["base_url"]
    name = draining_proxy["name"]

    result: dict = {}
    in_flight = threading.Thread(target=_post_chat, args=(base_url, result), daemon=True)
    in_flight.start()

    # Let the request reach the (slow) upstream before signalling.
    time.sleep(2.0)
    assert _container_state(name)["running"], "container exited before the SIGTERM was sent"

    subprocess.run(["docker", "kill", "--signal=SIGTERM", name], check=True, capture_output=True)

    # The drain must outlast the upstream, not truncate it.
    in_flight.join(timeout=UPSTREAM_DELAY_SECONDS + 30)
    assert not in_flight.is_alive(), "the in-flight request never finished"

    assert "error" not in result, (
        "SIGTERM severed an in-flight request instead of draining it: "
        f"{result.get('error')}\nlogs:\n{container_logs(name)}"
    )
    assert result["status"] == 200
    payload = json.loads(result["body"])
    content = payload["choices"][0]["message"]["content"]
    assert SECRET_EMAIL in content, (
        "the drained response was not rehydrated; the shutdown path returned a "
        f"still-masked body: {content!r}"
    )

    state = _wait_for_exit(name, timeout=40.0)
    assert state["oom_killed"] is False
    assert state["exit_code"] == 0, (
        f"container exited {state['exit_code']} rather than draining cleanly "
        f"(137 = SIGKILL, 143 = untrapped SIGTERM)\nlogs:\n{container_logs(name)}"
    )


def test_new_connections_are_refused_once_drain_has_started(draining_proxy):
    base_url = draining_proxy["base_url"]
    name = draining_proxy["name"]

    assert urllib.request.urlopen(f"{base_url}/healthz", timeout=5).getcode() == 200

    result: dict = {}
    in_flight = threading.Thread(target=_post_chat, args=(base_url, result), daemon=True)
    in_flight.start()
    time.sleep(2.0)

    subprocess.run(["docker", "kill", "--signal=SIGTERM", name], check=True, capture_output=True)
    time.sleep(1.5)

    # A pod being drained must stop taking new work. Either shape is correct:
    # the listener is closed (connection refused / reset), or the middleware
    # short-circuits with 429 + Retry-After.
    try:
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=5) as response:
            code = response.getcode()
            retry_after = response.headers.get("Retry-After")
        assert code == 429, f"a draining proxy accepted new work and answered {code}"
        assert retry_after is not None
    except urllib.error.HTTPError as exc:
        assert exc.code in (429, 503), f"unexpected status from a draining proxy: {exc.code}"
    except (urllib.error.URLError, ConnectionError, OSError):
        pass  # listener already closed -- the strongest form of "stop sending traffic"

    in_flight.join(timeout=UPSTREAM_DELAY_SECONDS + 30)
    _wait_for_exit(name, timeout=40.0)
