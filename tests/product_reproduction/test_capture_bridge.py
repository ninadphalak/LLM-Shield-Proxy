from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from benchmarks.product_reproduction.capture_bridge import CaptureBridge


def test_bridge_forwards_only_authenticated_requests_to_loopback_capture() -> None:
    received: list[bytes] = []

    class Capture(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = b'{"captured":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    capture = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    worker = threading.Thread(target=capture.serve_forever, daemon=True)
    worker.start()
    try:
        with CaptureBridge(backend_port=capture.server_port, upstream_key="synthetic-upstream-key") as bridge:
            assert bridge.host_port > 0
            with httpx.Client(trust_env=False, timeout=5) as client:
                denied = client.post(bridge.local_url + "/v1/chat/completions", content=b"untrusted")
                allowed = client.post(
                    bridge.local_url + "/v1/chat/completions",
                    headers={"Authorization": "Bearer synthetic-upstream-key"},
                    content=b"fixture-request",
                )
            assert denied.status_code == 403
            assert allowed.status_code == 200
            assert allowed.content == b'{"captured":true}'
            assert received == [b"fixture-request"]
            assert bridge.snapshot().forwarded_requests == 1
            assert bridge.snapshot().unauthorized_requests == 1
    finally:
        capture.shutdown()
        capture.server_close()
        worker.join(timeout=5)


def test_bridge_marks_capture_handoff_gap_as_invalid() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        backend_port = reservation.getsockname()[1]

    with CaptureBridge(backend_port=backend_port, upstream_key="synthetic-upstream-key") as bridge:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(
                bridge.local_url + "/v1/chat/completions",
                headers={"Authorization": "Bearer synthetic-upstream-key"},
                content=b"during-handoff",
            )
        assert response.status_code == 503
        assert bridge.snapshot().handoff_requests == 1


def test_bridge_rejects_duplicate_authorization_headers() -> None:
    with CaptureBridge(backend_port=1, upstream_key="synthetic-upstream-key") as bridge:
        with socket.create_connection(("127.0.0.1", bridge.host_port), timeout=5) as client:
            client.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Authorization: Bearer synthetic-upstream-key\r\n"
                b"authorization: Bearer synthetic-upstream-key\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            assert b"403 Forbidden" in client.recv(256)
        assert bridge.snapshot().unauthorized_requests == 1
        assert bridge.snapshot().handoff_requests == 0


def test_bridge_rejects_non_ascii_synthetic_key() -> None:
    with pytest.raises(ValueError, match="synthetic upstream key"):
        CaptureBridge(backend_port=1, upstream_key="synthétic-key")


def test_bridge_rejects_wildcard_listener() -> None:
    with pytest.raises(ValueError, match="bind host"):
        CaptureBridge(backend_port=1, upstream_key="synthetic-upstream-key", bind_host="0.0.0.0")
    with pytest.raises(ValueError, match="container host"):
        CaptureBridge(backend_port=1, upstream_key="synthetic-upstream-key", container_host="outside.example")
