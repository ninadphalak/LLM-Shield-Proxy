"""`pii-leak-benchmark submit` turns a finished report into a submission.

The two things it must never do are send the report and guess the gateway's name. The
first would put a base URL and a capture hostname on a public tracker; the second would
publish the model alias a run happened to route through as the product that was measured.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from pii_leak_benchmark import submit
from pii_leak_benchmark.cli import main as cli_main

# GitHub truncates an over-long prefilled issue URL without an error, so the failure shows
# up as a half-filled form rather than as a message. This is the budget that keeps the
# citation out of the query string.
URL_BUDGET = 6000


def _operator_run(**overrides):
    report = {
        "schema": "pii-leak-benchmark/operator-run/v1",
        "verdict": "LEAK",
        "target_version": "1.2.3",
        "contract": {"harness_version": "0.3.1", "model": "gpt-4o-mini", "instrument_sha256": "ab" * 16},
        "attestation": {"run_url": "https://github.com/o/r/actions/runs/42", "repository": "o/r"},
        "generated_at": "2026-09-19T00:00:00Z",
    }
    report.update(overrides)
    return report


def _research_report():
    return {
        "schema": "pii-leak-benchmark/http-profile/v1",
        "outcome": "fail",
        "passed": False,
        "implementation": {"name": "some-gateway", "version": "0.9"},
        "metrics": {"leak_rate": {"single_chunk": 0.25, "adversarial": 0.75}},
        "generated_at": "2026-09-19T00:00:00Z",
    }


@pytest.mark.parametrize("report", [_operator_run(), _research_report()])
def test_the_body_carries_every_section_for_either_report_shape(report):
    body = submit.build_body(report, citation="CITATION")
    for heading in submit.SUBMISSION_SECTIONS:
        assert f"### {heading}" in body
    assert "CITATION" in body


def test_the_verdict_is_read_under_either_spelling():
    assert "LEAK" in submit.build_body(_operator_run())
    # The research shape files the same fact under `outcome`.
    assert "fail" in submit.build_body(_research_report())


def test_the_gateway_name_is_left_blank_rather_than_guessed():
    """An operator run records the model alias, which is not the gateway under test."""
    body = submit.build_body(_operator_run())
    gateway = body.split("### Gateway", 1)[1].split("###", 1)[0].strip()
    assert gateway == ""
    assert "gpt-4o-mini" not in body
    assert submit.build_title(_operator_run()) == "Result: "


def test_a_recorded_name_is_used_when_the_report_has_one():
    assert submit.build_title(_research_report()) == "Result: some-gateway"
    assert "some-gateway" in submit.build_body(_research_report())


def test_the_prefilled_url_stays_inside_its_budget():
    long_run = _operator_run(target_version="v" * 400)
    assert len(submit.submission_url(long_run)) < URL_BUDGET


def test_the_citation_is_not_in_the_url():
    url = submit.submission_url(_operator_run())
    body = parse_qs(urlsplit(url).query)["body"][0]
    assert "pii-leak-benchmark citation" not in body
    assert "Paste the output" in body


@pytest.mark.parametrize(
    "hostile",
    ["back`tick", "pipe|value", "line\nbreak", "</details><script>", "`" * 12],
)
def test_a_hostile_target_version_does_not_break_the_body(hostile):
    from pii_leak_benchmark.cite import build_citation

    report = _operator_run(target_version=hostile)
    citation = build_citation(report)
    body = submit.build_body(report, citation=citation)
    # Every section still readable, which is what a broken block would cost.
    for heading in submit.SUBMISSION_SECTIONS:
        assert f"### {heading}" in body
    # And the citation's own fence rule held: no line inside it opens an unterminated span.
    assert body.count("### ") == len(submit.SUBMISSION_SECTIONS)


def test_a_hostile_target_version_does_not_break_the_job_summary():
    from pii_leak_benchmark import ci

    run = {"schema": "pii-leak-benchmark/operator-run/v1",
           "contract": {"profile": "p", "duty": "restore", "seed": "a"},
           "verdict": "LEAK", "reason": "r", "target_version": "```\n</details>",
           "entities": {}, "required_checks": {}, "coverage": []}
    summary = ci.render_summary(run)
    fenced = summary.split("<details><summary><b>Citation block", 1)[1]
    opening = fenced.split("\n")[2]
    # The fence is longer than the longest backtick run inside the block it opens, which
    # is the only rule that survives a value made of backticks.
    assert opening.startswith("```")
    assert opening in fenced.split(opening, 1)[1], "the fence never closes"


def test_dry_run_writes_nothing_and_reaches_no_network(tmp_path, monkeypatch, capsys):
    report = tmp_path / "current.json"
    report.write_text(json.dumps(_operator_run()), encoding="utf-8")

    def explode(*args, **kwargs):
        raise AssertionError("submit --dry-run must not reach out")

    monkeypatch.setattr(submit.webbrowser, "open", explode)
    monkeypatch.setattr(submit.subprocess, "run", explode)

    before = sorted(path.name for path in tmp_path.iterdir())
    assert submit.main([str(report), "--dry-run"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert "### Gateway" in capsys.readouterr().out


def test_print_url_opens_nothing(tmp_path, monkeypatch, capsys):
    report = tmp_path / "current.json"
    report.write_text(json.dumps(_operator_run()), encoding="utf-8")
    monkeypatch.setattr(
        submit.webbrowser, "open", lambda *a, **k: pytest.fail("nothing should open")
    )
    assert submit.main([str(report), "--print-url"]) == 0
    assert capsys.readouterr().out.startswith("https://github.com/")


def test_the_report_itself_is_never_sent(tmp_path, capsys):
    """The fields `submitting.md` tells people to redact must not leave the machine."""
    report = _operator_run()
    report["target"] = {"base_url": "https://acct-1234.gateway.example/v1"}
    report["capture"] = {"self_probe": {"advertised_url": "https://secret.tunnel.example"}}
    path = tmp_path / "current.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    submit.main([str(path), "--dry-run"])
    printed = capsys.readouterr().out
    assert "acct-1234" not in printed
    assert "secret.tunnel.example" not in printed


def test_an_unreadable_report_fails_without_a_traceback(tmp_path, capsys):
    broken = tmp_path / "current.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert submit.main([str(broken)]) == 1
    assert "Could not read the report" in capsys.readouterr().err


def test_a_missing_report_names_where_it_looked(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert submit.main([]) == 1
    assert "current.json" in capsys.readouterr().err


def test_the_report_is_found_where_ci_leaves_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pii-check").mkdir()
    (tmp_path / "pii-check" / "current.json").write_text(
        json.dumps(_operator_run()), encoding="utf-8"
    )
    assert submit.find_report(None).as_posix().endswith("pii-check/current.json")


def test_the_subcommand_is_reachable_from_the_cli(tmp_path, capsys):
    report = tmp_path / "current.json"
    report.write_text(json.dumps(_operator_run()), encoding="utf-8")
    assert cli_main(["submit", str(report), "--dry-run"]) == 0
    assert "### Citation block" in capsys.readouterr().out
