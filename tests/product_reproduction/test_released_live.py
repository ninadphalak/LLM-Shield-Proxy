from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from benchmarks.product_reproduction.capture_bridge import CaptureBridge
from benchmarks.product_reproduction.image import BuiltImage
from benchmarks.product_reproduction.released_config import render_released_config
from benchmarks.product_reproduction.released_runtime import (
    start_released_container,
    stop_released_container,
)

TEMPLATE = Path("benchmarks/product_reproduction/configs/llm-shield-proxy-response-on-v1.json")


@pytest.mark.slow
def test_released_image_routes_to_run_owned_capture(tmp_path: Path) -> None:
    image_id = os.environ.get("PRODUCT_REPRODUCTION_LIVE_IMAGE")
    run_suffix = os.environ.get("PRODUCT_REPRODUCTION_LIVE_SUFFIX")
    if not image_id or not run_suffix:
        pytest.skip("set explicit retained image ID and run suffix for the opt-in Docker smoke")
    received: list[bytes] = []

    class Capture(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = (
                b'{"id":"capture-smoke","object":"chat.completion","created":1,'
                b'"model":"capture","choices":[{"index":0,"message":'
                b'{"role":"assistant","content":"safe response"},"finish_reason":"stop"}]}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    worker = threading.Thread(target=backend.serve_forever, daemon=True)
    worker.start()
    image = BuiltImage(
        image_id=image_id, tag="retained-live-image", run_suffix=run_suffix,
        wheel_sha256="sha256:" + "0" * 64, base_image="retained-base",
        distributions=(), dependency_lock_sha256="sha256:" + "0" * 64,
        dependency_wheels=(),
    )
    try:
        with CaptureBridge(backend_port=backend.server_port, upstream_key="synthetic-upstream-key") as bridge:
            config = render_released_config(
                TEMPLATE, tmp_path / "rendered.json", capture_base_url=bridge.container_url,
                upstream_key="synthetic-upstream-key", virtual_key="synthetic-virtual-key",
            )
            runtime = start_released_container(image, environment=config.environment, working_dir=tmp_path)
            try:
                with httpx.Client(trust_env=False, timeout=5) as client:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        try:
                            health = client.get(runtime.base_url + "/health")
                            if health.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.5)
                    else:
                        pytest.fail("released container did not become healthy")
                    response = client.post(
                        runtime.base_url + "/v1/chat/completions",
                        headers={"Authorization": "Bearer synthetic-virtual-key"},
                        json={"model": "capture", "messages": [{"role": "user", "content": "hello"}], "stream": False},
                        timeout=65,
                    )
                assert response.status_code == 200, response.text[:200]
                assert received
                assert bridge.snapshot().forwarded_requests == 1
                assert bridge.snapshot().unauthorized_requests == 0
                assert bridge.snapshot().handoff_requests == 0
            finally:
                stop_released_container(runtime)
    finally:
        backend.shutdown()
        backend.server_close()
        worker.join(timeout=5)
