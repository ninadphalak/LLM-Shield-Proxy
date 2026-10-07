"""The flat command's summary says why nothing reached the capture.

A gateway that rejects the client key answers 401 to every request. The report records
that only in `checks.sse_validity.status_codes`; the summary printed `Passed: False` and
`Outcome: claim-unstated` and left the reader to open the JSON. The conformance landing
page opens with this command, so a stranger with a keyed gateway saw a non-verdict and no
reason. `selfcheck` already names the status and the flag; the flat summary now does too.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _keyed_gateway(probe_capture_port: int | None = None):
    """A gateway that answers 401 to everything, the way one with a wrong client key does.

    With `probe_capture_port`, it first sends the capture one request of its own that
    carries none of this run's values, the way a gateway's startup probe of its upstream
    does. That request is captured but never correlated with the run.
    """

    class _KeyedGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        probed = False

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length", "0")))
            if probe_capture_port is not None and not _KeyedGateway.probed:
                _KeyedGateway.probed = True
                _probe(probe_capture_port)
            body = json.dumps({"error": {"message": "Invalid Proxy API Key.", "type": "authentication_error"}}).encode()
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)

    return _KeyedGateway


def _probe(capture_port: int) -> None:
    import urllib.request

    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{capture_port}/v1/chat/completions",
                data=json.dumps({"model": "probe", "messages": [{"role": "user", "content": "ping"}]}).encode(),
                headers={"content-type": "application/json"},
            ),
            timeout=10,
        ).read()
    except Exception:  # noqa: BLE001
        pass


def _run_flat_command(gateway_port: int, capture_port: int, out_path) -> subprocess.CompletedProcess:
    environment = {**os.environ, "TELEMETRY_ENABLED": "false"}
    environment.pop("TELEMETRY_ENDPOINT_URL", None)
    environment.pop("CONFORMANCE_TARGET_API_KEY", None)
    return subprocess.run(
        [
            sys.executable, "-m", "pii_leak_benchmark.cli",
            "--target-base-url", f"http://127.0.0.1:{gateway_port}/v1",
            "--iterations", "1",
            "--capture-port", str(capture_port),
            "--json-out", str(out_path),
        ],
        capture_output=True, text=True, env=environment, timeout=300,
    )


def _run_against_keyed_gateway(tmp_path, with_probe: bool):
    capture_port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _keyed_gateway(capture_port if with_probe else None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = _run_flat_command(server.server_address[1], capture_port, tmp_path / "r.json")
    finally:
        server.shutdown()
        server.server_close()
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    return result, report


def _assert_reason_in_summary(result) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Passed:       False" in result.stdout
    # The reason, in the summary itself, with the one-flag fix.
    assert "answered HTTP 401" in result.stdout, result.stdout
    assert "--target-api-key" in result.stdout, result.stdout
    assert "CONFORMANCE_TARGET_API_KEY" in result.stdout, result.stdout


def test_a_gateway_that_rejects_the_key_is_named_in_the_summary(tmp_path):
    result, report = _run_against_keyed_gateway(tmp_path, with_probe=False)

    _assert_reason_in_summary(result)
    assert report["checks"]["sse_validity"]["status_codes"] == [401]
    assert report["checks"]["configured_upstream_boundary"]["captured_requests"] == 0


def test_an_unrelated_captured_request_does_not_hide_the_reason(tmp_path):
    """The line is gated on `correlated_requests`, the field attributability is derived
    from, not on `captured_requests`: a gateway that probes its upstream on startup puts
    one request on the capture that is not this run's, and that must not silence the
    explanation of why nothing else arrived."""
    result, report = _run_against_keyed_gateway(tmp_path, with_probe=True)

    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["captured_requests"] >= 1, boundary
    assert boundary["correlated_requests"] == 0, boundary
    _assert_reason_in_summary(result)
