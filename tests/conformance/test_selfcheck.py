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
    assert verdict == selfcheck.VERDICT_CHECK_FAILED
    assert "response_fidelity" in reason
    assert "not leaking" in reason


@pytest.mark.parametrize("counter", ["uninspectable", "unattributed_uninspectable"])
def test_partial_inspection_never_claims_containment(counter, capsys):
    report = _with_fixture(**{counter: 1}, leaked=["EMAIL"])
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    output = capsys.readouterr().out
    assert "contained" not in output
    assert output.count("not measured") == 2
    assert "LEAK" in output


def test_anonymize_duty_does_not_require_restoration():
    report = _report(passed=False, failing_checks=("response_fidelity",))
    assert selfcheck.verdict_for(report, duty="anonymize")[0] == selfcheck.VERDICT_CLEAN
    report["checks"]["sse_validity"]["passed"] = False
    assert selfcheck.verdict_for(report, duty="anonymize")[0] == selfcheck.VERDICT_CHECK_FAILED


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


def test_the_published_fixture_is_unchanged_by_the_credential_option() -> None:
    """Every published v1 row was measured against exactly three types.

    If `make_fixture()` ever starts returning more by default, those rows silently change
    meaning -- the stale-row failure this project refuses everywhere else. The credential
    set is additive and must stay opt-in.
    """
    from pii_leak_benchmark.http_profile import PROTECTED_ENTITY_TYPES, make_fixture

    assert sorted(make_fixture()) == ["CREDIT_CARD", "EMAIL", "SSN"]
    assert PROTECTED_ENTITY_TYPES == ("EMAIL", "SSN", "CREDIT_CARD")


def test_credential_specimens_round_trip_and_collide_with_nothing() -> None:
    """The two invariants that let a needle ship at all.

    Recoverable: the capture must be able to find the value it sent, or a leak reads as
    containment. Non-overlapping: no needle may be a substring of another, or one
    entity's finding can be satisfied by decoding a different entity -- which is exactly
    how the old SSN, a substring of the old card, masked a real decoder defect.
    """
    from pii_leak_benchmark.http_profile import _build_prompt, extract_fixture, make_fixture

    fixture = make_fixture(include_credentials=True)
    assert sorted(fixture) == [
        "AWS_ACCESS_KEY_ID", "CREDIT_CARD", "EMAIL", "GITHUB_TOKEN", "SLACK_TOKEN", "SSN",
    ]
    assert extract_fixture(_build_prompt("a-b-c-d-e", fixture)) == fixture

    values = list(fixture.values())
    overlapping = [(a, b) for a in values for b in values if a != b and a in b]
    assert overlapping == []


def test_credential_specimens_carry_their_non_live_markers() -> None:
    """Shipping a value that could be a live credential is the one unrecoverable mistake.

    Each specimen's safety rests on a literal -- AWS's own published example, and the
    EXAMPLE/NOTAREAL tokens with zeroed regions. Asserting the markers is a cheap tripwire
    against someone "improving" a fixture into something that could authenticate.
    """
    from pii_leak_benchmark.http_profile import _CREDENTIAL_VALUES

    assert _CREDENTIAL_VALUES["AWS_ACCESS_KEY_ID"] == "AKIAIOSFODNN7EXAMPLE"
    for entity in ("GITHUB_TOKEN", "SLACK_TOKEN"):
        assert "NOTAREAL" in _CREDENTIAL_VALUES[entity], entity
    # A Slack bot token's numeric fields are short ON PURPOSE: 13+ consecutive digits also
    # match this corpus's card pattern, which would have the credential result carried by
    # the PII detector instead.
    assert "-00000-00000-" in _CREDENTIAL_VALUES["SLACK_TOKEN"]


def test_a_run_without_credentials_still_warns_that_they_were_not_tested(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Coverage claims must follow the fixture, not the version of the tool.

    With `--no-credentials` the warning has to come back, or a narrower run inherits a
    wider run's coverage claim and an operator reads "credentials fine" from a run that
    sent none.
    """
    report = _report()
    report["fixture"] = {"formats": {"EMAIL": "x", "SSN": "y", "CREDIT_CARD": "z"}}
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    assert "credentials" in capsys.readouterr().out


def test_a_credential_run_stops_claiming_credentials_are_untested(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _report()
    report["fixture"] = {"formats": {"EMAIL": "x", "AWS_ACCESS_KEY_ID": "y"}}
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    out = capsys.readouterr().out
    assert "API keys" not in out
    # The categories that are still genuinely absent must survive.
    assert "health and clinical data" in out


def _with_fixture(**kwargs: Any) -> dict[str, Any]:
    report = _report(**kwargs)
    report["fixture"] = {"formats": {"EMAIL": "x", "SSN": "y", "CREDIT_CARD": "z"}}
    return report


def test_every_tested_type_is_listed_not_only_the_ones_that_leaked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bare verdict cannot tell an operator which data types were actually handled.

    Printing only leaks makes a clean run unreadable: nothing separates "SSN was tested
    and contained" from "SSN was never tested", and those support opposite decisions.
    """
    report = _with_fixture(leaked=["EMAIL"])
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    out = capsys.readouterr().out

    assert "EMAIL" in out and "LEAK" in out
    for contained in ("SSN", "CREDIT_CARD"):
        assert contained in out
    assert out.count("contained") == 2


def test_an_unattributable_run_never_reports_a_type_as_contained(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The NOT MEASURED trap, in its second hiding place.

    If no traffic was inspected, no entity was contained -- it was not looked at. Marking
    them "contained" here would reintroduce, per row, exactly the false assurance the
    verdict ordering exists to prevent.
    """
    report = _with_fixture(correlated=0)
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    out = capsys.readouterr().out

    assert "contained" not in out
    assert out.count("not measured") == 3


def test_the_report_names_what_it_did_not_test(capsys: pytest.CaptureFixture[str]) -> None:
    """The dangerous reading of a clean run is "my gateway handles sensitive data".

    The fixture covers three shapes. Credentials and health data are the categories an
    operator will assume were included unless the output says otherwise.
    """
    report = _with_fixture()
    selfcheck._print_per_entity(report, report["checks"]["configured_upstream_boundary"])
    out = capsys.readouterr().out

    assert "Not tested by this profile" in out
    assert "credentials" in out
    assert "health" in out


@pytest.mark.slow
def test_an_unwritable_report_path_is_not_measured_rather_than_a_leak(tmp_path: Path) -> None:
    """An I/O failure must not borrow the exit status that means "your gateway leaked".

    The write used to sit outside the error handling, so an unwritable `--json-out` let
    the OSError escape and the process exited 1 -- the documented LEAK status. An operator
    would have read "raw values reached your upstream" when a directory was missing.

    `--json-out` points at a path whose parent is a FILE, which cannot be created as a
    directory on any platform.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "pii_leak_benchmark.cli", "selfcheck",
            "--target-base-url", "capture://self",
            "--iterations", "1",
            "--json-out", str(blocker / "report.json"),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=300,
    )
    assert result.returncode == selfcheck.EXIT_NOT_MEASURED, result.stdout + result.stderr
    assert selfcheck.VERDICT_NOT_MEASURED in result.stderr
    # `capture://self` really does leak, so without the fix this would exit 1 for the
    # right-looking reason and the regression would be invisible.
    assert selfcheck.VERDICT_LEAK not in result.stdout


def test_cli_headers_add_to_the_environment_rather_than_replacing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials and routing headers live in CONFORMANCE_TARGET_HEADERS.

    The flat command merges both sources. `selfcheck` used to read the environment only
    when no `--target-header` was passed, so adding one CLI header dropped every
    environment header -- running the check unauthenticated, or against a different route,
    and then blaming the gateway for the resulting NOT MEASURED.
    """
    monkeypatch.setenv("CONFORMANCE_TARGET_HEADERS", "X-Env-One=1\nX-Env-Two=2")
    args = selfcheck.build_parser().parse_args(
        ["--target-base-url", "http://x/v1", "--target-header", "X-Cli=3"]
    )
    assert args.target_header == ["X-Env-One=1", "X-Env-Two=2", "X-Cli=3"]


def test_the_environment_alone_still_supplies_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFORMANCE_TARGET_HEADERS", "X-Only=1")
    args = selfcheck.build_parser().parse_args(["--target-base-url", "http://x/v1"])
    assert args.target_header == ["X-Only=1"]


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


def _evidence(*entity_types: str) -> list[dict[str, str]]:
    return [
        {"entity_type": e, "match": "literal", "scope": "per-request", "channel": "all"}
        for e in entity_types
    ]


def test_a_long_entity_name_does_not_run_into_the_next_column(capsys) -> None:
    """A fixed width fails silently and only for the long row, so measure the rows.

    `AWS_ACCESS_KEY_ID` is 17 characters and printed `AWS_ACCESS_KEY_IDliteral`
    against a 14-wide column, while the five shorter names stayed legible. Widening
    the literal only moves the collision to the next name that outgrows it.
    """
    selfcheck._print_leak_evidence(_evidence("SSN", "AWS_SECRET_ACCESS_KEY"))
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.strip().startswith("ENTITY"))
    rows = [line for line in lines if "per-request" in line]
    assert len(rows) == 2
    match_column = header.index("MATCH")
    for row in rows:
        # The name must end before MATCH begins, and every row must start MATCH at
        # the same offset as the header - a wider name may not shove the column.
        assert row.index("literal") == match_column, row
