"""The checker against servers whose behaviour is known, so every verdict is accounted for."""

import json
import socket

import pytest
from mcp_ssrf_check.cli import build_parser, main, run
from mcp_ssrf_check.report import EXIT_FAIL, EXIT_INCONCLUSIVE, EXIT_OK, FAIL, INCONCLUSIVE, INFO, PASS, SKIP

from .fake_server import MODES, FakeMcpServer


def _run(url, *extra):
    args = build_parser().parse_args(["--url", url, "--settle", "0.05", *extra])
    return run(args)


def _by_id(report):
    return {check.id: check for check in report.checks}


def test_hardened_stateful_server_passes_every_check():
    with FakeMcpServer() as server:
        report = _run(server.url, "--fetch-tool", "fetch")
    checks = _by_id(report)
    assert report.lifecycle == "stateful"
    assert checks["baseline"].status == PASS
    assert checks["host-header"].status == PASS
    assert checks["origin-header"].status == PASS
    assert checks["origin-null"].status == INFO
    assert checks["session-binding"].status == PASS
    assert checks["tool-url-ssrf"].status == PASS, checks["tool-url-ssrf"].detail
    assert report.exit_code() == EXIT_OK
    outcomes = {k: v["outcome"] for k, v in checks["tool-url-ssrf"].evidence["spellings"].items()}
    assert set(outcomes.values()) <= {"refused", "not-exercised", "not-requested"}, outcomes


def test_weak_server_fails_host_origin_session_and_ssrf():
    with FakeMcpServer(**MODES["weak"]) as server:
        report = _run(server.url, "--fetch-tool", "fetch", "--redirect-target", "127.0.0.1")
    checks = _by_id(report)
    assert checks["host-header"].status == FAIL
    assert checks["origin-header"].status == FAIL
    assert checks["session-binding"].status == FAIL
    assert "fabricated session id was honoured" in checks["session-binding"].detail
    ssrf = checks["tool-url-ssrf"]
    assert ssrf.status == FAIL
    spellings = ssrf.evidence["spellings"]
    assert spellings["direct"]["outcome"] == "reached"
    assert spellings["localhost"]["outcome"] == "reached"
    # A weak server fetches 127.0.0.1 directly, so the redirect probe cannot show anything on it.
    assert spellings["redirect"]["outcome"] == "not-exercised", spellings["redirect"]
    assert report.exit_code() == EXIT_FAIL


def test_verdicts_name_what_was_sent_and_not_what_came_back():
    """Evidence carries status codes and message shapes only, never a body the server returned:
    not a tool result, not an initialize result, and not a rejection page either."""
    with FakeMcpServer(fetch_guard="none", html_errors=True) as server:
        report = _run(server.url, "--fetch-tool", "fetch")
    dumped = json.dumps(report.to_dict())
    assert "TOOL-OUTPUT-MARKER" not in dumped
    assert "serverInfo" not in dumped
    assert "DEBUG-PAGE-MARKER" not in dumped
    assert "/srv/app" not in dumped
    checks = _by_id(report)
    assert checks["host-header"].status == PASS
    assert checks["host-header"].evidence["response"]["body"] == "other"
    assert checks["host-header"].evidence["response"]["content_type"] == "text/html"


def test_redirect_probe_catches_a_guard_that_checks_only_the_first_hop():
    """The string guard allows the hostname spelling and blocks 127.0.0.1, so a redirect from
    localhost into 127.0.0.1 is exactly the hop it fails to re-check."""
    with FakeMcpServer(fetch_guard="string") as server:
        report = _run(server.url, "--fetch-tool", "fetch", "--callback-host", "localhost", "--redirect-target", "127.0.0.1")
    spellings = _by_id(report)["tool-url-ssrf"].evidence["spellings"]
    assert spellings["redirect"]["outcome"] == "reached", spellings["redirect"]
    assert spellings["redirect"]["target"] == "127.0.0.1"


def test_redirect_probe_is_not_run_without_a_target():
    with FakeMcpServer(fetch_guard="none") as server:
        report = _run(server.url, "--fetch-tool", "fetch")
    spellings = _by_id(report)["tool-url-ssrf"].evidence["spellings"]
    assert spellings["redirect"]["outcome"] == "not-requested"


def test_stateless_server_skips_sessions_and_still_checks_headers():
    with FakeMcpServer(stateless=True) as server:
        report = _run(server.url)
    checks = _by_id(report)
    assert report.lifecycle == "stateless"
    assert report.protocol_version == "2026-07-28"
    assert checks["host-header"].status == PASS
    assert checks["origin-header"].status == PASS
    assert checks["session-binding"].status == SKIP


def test_string_matching_guard_is_caught_by_the_hostname_spelling():
    with FakeMcpServer(fetch_guard="string") as server:
        report = _run(server.url, "--fetch-tool", "fetch")
    ssrf = _by_id(report)["tool-url-ssrf"]
    assert ssrf.status == FAIL
    spellings = ssrf.evidence["spellings"]
    assert spellings["direct"]["outcome"] == "refused"
    assert spellings["localhost"]["outcome"] == "reached"


def test_ssrf_check_is_skipped_unless_a_tool_is_named():
    with FakeMcpServer() as server:
        report = _run(server.url)
    ssrf = _by_id(report)["tool-url-ssrf"]
    assert ssrf.status == SKIP
    assert "--fetch-tool" in ssrf.detail
    assert report.exit_code() == EXIT_OK


def test_sse_framed_responses_are_read():
    with FakeMcpServer(sse=True) as server:
        report = _run(server.url, "--fetch-tool", "fetch")
    checks = _by_id(report)
    assert report.lifecycle == "stateful"
    assert checks["baseline"].status == PASS
    assert checks["host-header"].status == PASS
    assert checks["tool-url-ssrf"].status == PASS


def test_wrong_argument_name_is_inconclusive_not_a_pass():
    with FakeMcpServer() as server:
        report = _run(server.url, "--fetch-tool", "fetch", "--url-argument", "nope")
    ssrf = _by_id(report)["tool-url-ssrf"]
    assert ssrf.status == INCONCLUSIVE
    assert "--url-argument" in ssrf.detail
    assert report.exit_code() == EXIT_INCONCLUSIVE


def test_control_url_proves_the_wiring():
    with FakeMcpServer(fetch_guard="none") as server:
        report = _run(server.url, "--fetch-tool", "fetch", "--control-url", f"http://127.0.0.1:{server.port}/mcp")
    wiring = _by_id(report)["tool-wiring"]
    # The fake server answers GET /mcp with 405; urllib raises, the tool reports isError, and
    # the wiring check says so rather than claiming success.
    assert wiring.status == INCONCLUSIVE


def test_server_without_delete_support_is_not_failed_for_it():
    with FakeMcpServer(delete_supported=False) as server:
        report = _run(server.url)
    session = _by_id(report)["session-binding"]
    assert session.status == PASS
    assert session.evidence["terminated_session"].startswith("not exercised")


def test_unreachable_target_exits_inconclusive(capsys):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    code = main(["--url", f"http://127.0.0.1:{port}/mcp", "--timeout", "2"])
    assert code == EXIT_INCONCLUSIVE
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_json_report_carries_schema_target_and_summary(tmp_path, capsys):
    out = tmp_path / "report.json"
    with FakeMcpServer() as server:
        code = main(["--url", server.url, "--settle", "0.05", "--json-out", str(out)])
    assert code == EXIT_OK
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "mcp-ssrf-check/1"
    assert data["target"].endswith("/mcp")
    assert data["summary"]["fail"] == 0
    assert {c["id"] for c in data["checks"]} >= {"baseline", "host-header", "origin-header", "session-binding", "tool-url-ssrf"}
    assert "report written to" in capsys.readouterr().out


def test_unknown_skip_name_is_rejected():
    with pytest.raises(SystemExit):
        _run("http://127.0.0.1:1/mcp", "--skip", "hots")


def test_bearer_is_read_from_the_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv("MCP_SSRF_CHECK_BEARER", "env-token")
    with FakeMcpServer() as server:
        _run(server.url, "--skip", "host,origin,origin-null,session")
    assert server.authorization_headers
    assert set(server.authorization_headers) == {"Bearer env-token"}


def test_bearer_flag_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("MCP_SSRF_CHECK_BEARER", "env-token")
    with FakeMcpServer() as server:
        _run(server.url, "--bearer", "flag-token", "--skip", "host,origin,origin-null,session")
    assert set(server.authorization_headers) == {"Bearer flag-token"}


def test_markdown_summary_is_one_row_per_check(tmp_path):
    out = tmp_path / "summary.md"
    with FakeMcpServer(**MODES["weak"]) as server:
        code = main(["--url", server.url, "--settle", "0.05", "--markdown-out", str(out)])
    assert code == EXIT_FAIL
    text = out.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
    assert len(rows) == 6, rows
    assert "| FAIL | `host-header` |" in text
    assert "| SKIP | `tool-url-ssrf` |" in text
    assert "Summary: " in text


def test_markdown_cells_cannot_break_the_table():
    from mcp_ssrf_check.report import CheckResult, Report

    report = Report("http://x/mcp`|", "stateful", "v", [CheckResult("a", "t", PASS, "one | two\nthree")], "0", "now")
    row = next(line for line in report.render_markdown().splitlines() if "`a`" in line)
    assert row == "| PASS | `a` | one \\| two three |"
    assert "Target `http://x/mcp\\|`" in report.render_markdown()
