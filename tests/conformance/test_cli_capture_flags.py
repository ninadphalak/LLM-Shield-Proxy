"""Public capture mode from the CLI, and the capture token never escaping.

Before this, `--capture-host 0.0.0.0` was a dead end: the harness aborted on a
non-loopback bind without a public URL, and there was no flag to supply one. The error
also named Python parameters a CLI user cannot type.

The token requirement is the sharper one. It authenticates the target's traffic to a
capture that is on the open internet, so it is a credential. It must not reach a
published artifact, and it must not reach an error message either -- artifacts and
stderr both get pasted into issues. Process listings show argv, so the environment
variable is preferred and wins over the flag.
"""

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pii_leak_benchmark.cli import build_parser
from pii_leak_benchmark.http_profile import (
    extract_fixture,
    run_http_conformance,
)

TOKEN = "s3cret-capture-token-do-not-publish"
# Per-request now; see extract_fixture.


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


def _gateway(port, token=None):
    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            import urllib.request

            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
            prompt = payload["messages"][-1]["content"]
            # Values vary per run: recover them from the prompt by format.
            RAW = list(extract_fixture(prompt).values())
            MASK = {value: f"[TOK_{index}]" for index, value in enumerate(RAW)}
            masked = prompt
            for raw, tok in MASK.items():
                masked = masked.replace(raw, tok)
            payload["messages"][-1]["content"] = masked
            headers = {"content-type": "application/json"}
            if token:
                headers["authorization"] = f"Bearer {token}"
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=json.dumps(payload).encode(),
                        headers=headers,
                    ),
                    timeout=30,
                ).read()
            except Exception:  # noqa: BLE001
                pass
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                self.wfile.write(
                    b"data: "
                    + json.dumps({"choices": [{"delta": {"content": character}}]}).encode()
                    + b"\n\n"
                )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Gateway


# ------------------------------------------------------------------ flag surface


def test_capture_flags_exist():
    parser = build_parser(require_target=False)
    options = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert "--capture-token" in options
    assert "--capture-public-url" in options


def test_capture_public_url_reads_its_environment_variable(monkeypatch):
    monkeypatch.setenv("CONFORMANCE_CAPTURE_PUBLIC_URL", "https://tunnel.example/v1")
    args = build_parser(require_target=False).parse_args([])
    assert args.capture_public_url == "https://tunnel.example/v1"


# ------------------------------------------------- the dead end that was created


def test_non_loopback_bind_error_names_the_cli_flag():
    """The message must name something a CLI user can type."""
    with pytest.raises(ValueError) as excinfo:
        run_http_conformance(
            "http://127.0.0.1:1/v1", iterations=1, capture_host="0.0.0.0", capture_port=_free_port()
        )
    message = str(excinfo.value)
    assert "--capture-public-url" in message
    assert "CONFORMANCE_CAPTURE_PUBLIC_URL" in message


def test_missing_token_error_names_the_cli_flag():
    with pytest.raises(ValueError) as excinfo:
        run_http_conformance(
            "http://127.0.0.1:1/v1",
            iterations=1,
            capture_host="0.0.0.0",
            capture_port=_free_port(),
            capture_public_url="https://tunnel.example/v1",
        )
    message = str(excinfo.value)
    assert "--capture-token" in message
    assert "CONFORMANCE_CAPTURE_TOKEN" in message


def test_public_mode_is_reachable_from_the_cli(tmp_path, capture_port):
    """End to end through `benchmark`, which was Python-API only before."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(capture_port, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "report.json"
    try:
        environment = {
            **os.environ,
            "TELEMETRY_ENABLED": "false",
            "CONFORMANCE_CAPTURE_TOKEN": TOKEN,
        }
        environment.pop("TELEMETRY_ENDPOINT_URL", None)
        result = subprocess.run(
            [
                sys.executable, "-m", "pii_leak_benchmark.cli",
                "--target-base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--iterations", "1",
                "--capture-port", str(capture_port),
                "--capture-public-url", f"http://127.0.0.1:{capture_port}/v1",
                "--json-out", str(out),
            ],
            capture_output=True, text=True, env=environment, timeout=300,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["capture"]["mode"] == "public"
    assert report["capture"]["authentication_required"] is True
    assert report["checks"]["configured_upstream_boundary"]["correlated_requests"] == 1


# --------------------------------------------------------------- token secrecy


def test_token_never_appears_in_the_report(capture_port):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(capture_port, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        report = run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=capture_port,
            capture_token=TOKEN,
            capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
        )
    finally:
        server.shutdown()
        server.server_close()
    # Serialized, so a token nested anywhere -- a header string the capture recorded,
    # a probe URL, an error field -- is caught, not just the fields we thought of.
    assert TOKEN not in json.dumps(report)
    assert report["capture"]["authentication_required"] is True


def test_token_never_appears_in_an_error_message(capture_port):
    """Errors get pasted into issues as readily as artifacts do.

    Driven through a real abort: another process holds the capture port, so the
    self-probe fails closed while a token is configured.
    """

    class Squatter(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_GET(self):  # noqa: N802
            body = b'{"squatter": true}'
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    squatter = ThreadingHTTPServer(("127.0.0.1", capture_port), Squatter)
    threading.Thread(target=squatter.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OSError) as excinfo:
            run_http_conformance(
                "http://127.0.0.1:1/v1",
                iterations=1,
                capture_port=capture_port,
                capture_token=TOKEN,
                capture_public_url=f"http://127.0.0.1:{capture_port}/v1",
                timeout_seconds=2.0,
            )
    finally:
        squatter.shutdown()
        squatter.server_close()
    assert TOKEN not in str(excinfo.value)


def test_no_error_path_can_interpolate_the_token():
    """Structural: no raise site in the module may format the token into its message.

    The abort test above exercises one path. This one covers the paths a future edit
    might add, by reading the source rather than by hoping every branch was hit.
    """
    source = Path(
        run_http_conformance.__globals__["__file__"]
    ).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "capture_token" in line and ("raise " in line or line.strip().startswith('f"'))
    ]
    interpolating = [line for line in offenders if "{capture_token" in line]
    assert not interpolating, interpolating


def test_environment_token_wins_over_the_flag(tmp_path, capture_port):
    """argv is world-readable in a process listing; the env var must take precedence.

    The gateway forwards the ENV token. If the flag won, the capture would reject that
    traffic as unattributed and nothing would correlate.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(capture_port, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "report.json"
    try:
        environment = {
            **os.environ,
            "TELEMETRY_ENABLED": "false",
            "CONFORMANCE_CAPTURE_TOKEN": TOKEN,
        }
        environment.pop("TELEMETRY_ENDPOINT_URL", None)
        result = subprocess.run(
            [
                sys.executable, "-m", "pii_leak_benchmark.cli",
                "--target-base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--iterations", "1",
                "--capture-port", str(capture_port),
                "--capture-public-url", f"http://127.0.0.1:{capture_port}/v1",
                "--capture-token", "a-different-and-wrong-token",
                "--json-out", str(out),
            ],
            capture_output=True, text=True, env=environment, timeout=300,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["correlated_requests"] == 1, "the env token did not win"
    assert boundary["unattributed_requests"] == 0
    assert "a-different-and-wrong-token" not in out.read_text(encoding="utf-8")


def test_cli_prints_the_outcome_and_explains_a_non_verdict(tmp_path, capture_port):
    """stdout must not let a reader write a Fail row from a non-verdict run."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(capture_port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        environment = {**os.environ, "TELEMETRY_ENABLED": "false"}
        environment.pop("TELEMETRY_ENDPOINT_URL", None)
        result = subprocess.run(
            [
                sys.executable, "-m", "pii_leak_benchmark.cli",
                "--target-base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--iterations", "1",
                "--capture-port", str(capture_port),
                "--redaction-claimed", "not-offered",
                "--redaction-claim-citation", "https://example.invalid/docs",
                "--json-out", str(tmp_path / "r.json"),
            ],
            capture_output=True, text=True, env=environment, timeout=300,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Outcome:      not-applicable" in result.stdout
    assert "never offered" in result.stdout


def test_cli_claim_flags_reach_the_report(tmp_path, capture_port):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(capture_port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "r.json"
    try:
        environment = {**os.environ, "TELEMETRY_ENABLED": "false"}
        environment.pop("TELEMETRY_ENDPOINT_URL", None)
        result = subprocess.run(
            [
                sys.executable, "-m", "pii_leak_benchmark.cli",
                "--target-base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--iterations", "1",
                "--capture-port", str(capture_port),
                "--redaction-claimed", "claimed",
                "--redaction-claim-citation", "https://example.invalid/guardrails",
                "--redaction-enabled",
                "--redaction-config-reference", "guardrail=pii-redact",
                "--json-out", str(out),
            ],
            capture_output=True, text=True, env=environment, timeout=300,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    claim = json.loads(out.read_text(encoding="utf-8"))["redaction_claim"]
    assert claim["vendor_claims_pii_redaction"] == "claimed"
    assert claim["configured_for_this_run"] is True
    assert claim["claim_citation"] == "https://example.invalid/guardrails"
    assert claim["configuration_reference"] == "guardrail=pii-redact"


def test_a_claim_without_a_citation_fails_the_cli_cleanly(tmp_path, capture_port):
    environment = {**os.environ, "TELEMETRY_ENABLED": "false"}
    environment.pop("TELEMETRY_ENDPOINT_URL", None)
    result = subprocess.run(
        [
            sys.executable, "-m", "pii_leak_benchmark.cli",
            "--target-base-url", "http://127.0.0.1:1/v1",
            "--iterations", "1",
            "--capture-port", str(capture_port),
            "--redaction-claimed", "claimed",
            "--json-out", str(tmp_path / "r.json"),
        ],
        capture_output=True, text=True, env=environment, timeout=300,
    )
    assert result.returncode == 2
    assert "claim_citation is required" in result.stderr
    assert not Path(tmp_path / "r.json").exists()
