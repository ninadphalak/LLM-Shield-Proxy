"""The operator smoke test must never read a silent misconfiguration as a clean result.

`selfcheck` exists for one audience: someone pointing the harness at their own gateway to
answer "does my deployment leak?". Its whole value is the verdict and the exit status, so
the ways of destroying that value are what this file pins.

THE ONE THAT MATTERS. A gateway that answers the client plausibly but never routes to the
capture inspects nothing, so every needle check passes VACUOUSLY. If that reads as CLEAN,
an operator ships a leaking deployment on the strength of this tool. `verdict_for` orders
attributability ahead of the leak test for exactly this reason, and
`test_nothing_reaching_the_capture_is_not_clean` fails if that ordering is ever reversed.

The end-to-end pair is slow and network-backed, so both are marked `slow`. They are worth
their runtime: the unit tests prove the derivation is right about a dict, and only the
end-to-end ones prove the command actually produces such a dict from a real run. The
documentation promises both exit statuses by number.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pii_leak_benchmark import selfcheck

REPO_ROOT = Path(__file__).resolve().parents[2]


def _report(
    *,
    correlated: int = 2,
    leaked: list[str] | None = None,
    unattributed_leaked: list[str] | None = None,
    uninspectable: int = 0,
    unattributed_uninspectable: int = 0,
    passed: bool = True,
    failing_checks: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The smallest report shape `verdict_for` reads, with everything else healthy."""
    checks: dict[str, Any] = {
        "configured_upstream_boundary": {
            "correlated_requests": correlated,
            "uninspectable_requests": uninspectable,
            "unattributed_uninspectable_requests": unattributed_uninspectable,
            "leaked_entity_types": leaked or [],
            "unattributed_leaked_entity_types": unattributed_leaked or [],
            "passed": not (leaked or unattributed_leaked),
        },
        "response_fidelity": {"passed": "response_fidelity" not in failing_checks},
        "sse_validity": {"passed": "sse_validity" not in failing_checks},
    }
    return {"passed": passed, "checks": checks}


# --------------------------------------------------------------------------- unit


def test_a_healthy_run_with_no_leak_is_clean() -> None:
    verdict, _ = selfcheck.verdict_for(_report())
    assert verdict == selfcheck.VERDICT_CLEAN


def test_nothing_reaching_the_capture_is_not_clean() -> None:
    """The central trap: zero correlated requests, every check vacuously passing.

    This is what a gateway that was never pointed at the capture produces. Reporting it
    as CLEAN is the single failure mode that actively misleads an operator, so it must
    resolve to NOT MEASURED even though nothing is recorded as having leaked.
    """
    verdict, reason = selfcheck.verdict_for(_report(correlated=0))
    assert verdict == selfcheck.VERDICT_NOT_MEASURED
    assert "marker" in reason


def test_attributability_is_decided_before_leakage() -> None:
    """Ordering, stated as its own test so a refactor cannot quietly invert it.

    A report with no correlated requests AND recorded leaks is still NOT MEASURED: if
    nothing was attributable, the harness cannot say whose traffic it saw.
    """
    verdict, _ = selfcheck.verdict_for(_report(correlated=0, leaked=["EMAIL"]))
    assert verdict == selfcheck.VERDICT_NOT_MEASURED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uninspectable": 1},
        {"unattributed_uninspectable": 1},
    ],
)
def test_traffic_that_could_not_be_inspected_fails_closed(kwargs: dict[str, int]) -> None:
    verdict, reason = selfcheck.verdict_for(_report(**kwargs))
    assert verdict == selfcheck.VERDICT_NOT_MEASURED
    assert "inspected" in reason


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"leaked": ["EMAIL", "SSN"]}, "EMAIL, SSN"),
        ({"unattributed_leaked": ["CREDIT_CARD"]}, "CREDIT_CARD"),
    ],
)
def test_a_recorded_leak_names_the_entity_types(kwargs: dict[str, Any], expected: str) -> None:
    verdict, reason = selfcheck.verdict_for(_report(**kwargs))
    assert verdict == selfcheck.VERDICT_LEAK
    assert expected in reason


def test_a_failed_check_without_a_leak_says_so_rather_than_crying_leak() -> None:
    """A gateway that masks and never restores is broken, not leaking.

    The verdict is still non-zero, because the operator must act. The reason has to say
    which it was, or a fidelity bug gets reported to a vendor as a privacy failure.
    """
    verdict, reason = selfcheck.verdict_for(
        _report(passed=False, failing_checks=("response_fidelity",))
    )
    assert verdict == selfcheck.VERDICT_LEAK
    assert "response_fidelity" in reason
    assert "not leaking" in reason


def test_the_exit_statuses_are_the_ones_the_documentation_promises() -> None:
    # Operators wire these into CI. They are API.
    assert (selfcheck.EXIT_CLEAN, selfcheck.EXIT_LEAK, selfcheck.EXIT_NOT_MEASURED) == (0, 1, 2)


# --------------------------------------------------------------------- end to end


class _NeverForwards(BaseHTTPRequestHandler):
    """Answers the client with a valid stream and never contacts the capture.

    The shape of a real misconfiguration: the client sees a healthy response, so nothing
    about the run looks wrong from the outside.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        events = "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": piece}, "index": 0}]}) + "\n\n"
            for piece in ("Sure", ", ", "here", ".")
        )
        body = (events + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _run_selfcheck(target: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "pii_leak_benchmark.cli", "selfcheck",
            "--target-base-url", target,
            "--iterations", "1",
            "--json-out", str(tmp_path / "report.json"),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=300,
    )


@pytest.mark.slow
def test_the_documented_floor_reports_a_leak(tmp_path: Path) -> None:
    """`capture://self` is the no-gateway control the docs tell operators to run first.

    If this ever reports anything else, the capture is not seeing traffic and no other
    run from that setup means anything -- which is exactly why the docs make it step one.
    """
    result = _run_selfcheck("capture://self", tmp_path)
    assert result.returncode == selfcheck.EXIT_LEAK, result.stdout + result.stderr
    assert selfcheck.VERDICT_LEAK in result.stdout


@pytest.mark.slow
def test_a_gateway_that_never_forwards_is_reported_as_not_measured(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NeverForwards)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = _run_selfcheck(f"http://127.0.0.1:{port}/v1", tmp_path)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == selfcheck.EXIT_NOT_MEASURED, result.stdout + result.stderr
    assert selfcheck.VERDICT_NOT_MEASURED in result.stdout
    # The operator must not be told which of the three causes it was, because the harness
    # genuinely cannot tell them apart from here.
    assert "indistinguishable" in result.stdout
