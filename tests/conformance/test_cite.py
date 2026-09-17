"""`pii-leak-benchmark cite` renders a citable block from a finished report.

The point of the subcommand is that a result posted in an issue names the instrument
that produced it, so these tests pin the fields a reader needs to rerun the run, and
pin the self-reported caveat so it cannot be dropped into something that sounds like
third-party attestation.
"""

from __future__ import annotations

import json

import pytest
from pii_leak_benchmark.cite import build_citation, main


def _report(**overrides):
    report = {
        "schema": "llm-shield.streaming-privacy-http-profile/v2.0.0",
        "harness_revision": "0.2.1",
        "generated_at": "2026-09-09T15:36:46Z",
        "outcome": "fail",
        "instrument": {"inspector_sha256": "94262e29a492ab6a"},
        "corpus": {"sha256": "30efa2eb658888448b4416bb", "case_count": 32, "seed": "a1b2c3d4"},
        "implementation": {"name": "reference-policy:chunk-local", "version": "0.2.1"},
        "environment": {"platform": "Windows-11", "python": "3.14.7"},
        "metrics": {
            "leak_rate": {"single_chunk": 0.125, "adversarial": 1.0},
            "delta_frag": 0.875,
            "fidelity_rate": 1.0,
            "cases_applicable": 32,
            "cases_inconclusive": 0,
        },
    }
    report.update(overrides)
    return report


def test_cite_names_the_instrument_that_produced_the_numbers():
    out = build_citation(_report())
    # Without these a reader cannot rerun the same scorer over the same inputs.
    assert "0.2.1" in out
    assert "94262e29a492ab6a" in out
    assert "reference-policy:chunk-local" in out
    assert "a1b2c3d4" in out


def test_rates_render_as_rates_not_counts():
    out = build_citation(_report())
    # 1.0 must not print as a bare "1" beside 0.125; it reads as a count.
    assert "`1.00`" in out
    assert "`0.125`" in out
    assert "`1`" not in out


def test_self_reported_caveat_is_always_present():
    for style in ("markdown", "text"):
        out = build_citation(_report(), style=style)
        assert "Self-reported" in out or "self-reported" in out
        # Never imply a verifier checked this.
        assert "verified" not in out.lower()
        assert "attested" not in out.lower().replace("not independently attested", "")


def test_missing_fields_degrade_instead_of_raising():
    out = build_citation({"metrics": {}})
    assert "unrecorded" in out


def test_operator_report_omits_research_only_rows():
    # Operator runs carry no scorer or corpus digests. Padding their block with
    # "unrecorded" rows makes a usable result look like a broken one.
    operator = {
        "schema": "llm-shield.streaming-privacy-http-profile/v1.0.0",
        "harness_revision": "1cef0ff8661eb89ec60c94f077332a1c55e44679",
        "implementation": {"name": "litellm", "version": "1.99.0"},
        "environment": {"platform": "Windows-11", "python": "3.14.7"},
        "outcome": "redaction-not-enabled",
        "passed": False,
    }
    out = build_citation(operator)
    assert "Inspector digest" not in out
    assert "Corpus digest" not in out
    assert "Corpus cases" not in out
    assert "litellm" in out
    assert "redaction-not-enabled" in out
    assert "Checks passed" in out


def test_research_report_keeps_the_digest_rows():
    out = build_citation(_report())
    assert "Inspector digest" in out
    assert "Corpus digest" in out


def test_package_version_is_reported_and_labelled():
    from pii_leak_benchmark import __version__

    out = build_citation(_report())
    assert "Benchmark package" in out
    assert __version__ in out
    # The caveat must say what that version actually means.
    assert "rendered this block" in out


def test_ci_provenance_is_surfaced_when_present():
    out = build_citation(
        _report(
            attestation={
                "repository": "acme/gateway",
                "run_url": "https://github.com/acme/gateway/actions/runs/1",
            }
        )
    )
    assert "acme/gateway" in out
    assert "actions/runs/1" in out


def test_text_style_has_no_markdown_table():
    out = build_citation(_report(), style="text")
    assert "|---|" not in out
    assert "Harness revision" in out


def test_cli_reads_a_report_file(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    assert main([str(path)]) == 0
    assert "94262e29a492ab6a" in capsys.readouterr().out


@pytest.mark.parametrize(
    "contents, expected",
    [("not json", 2), (json.dumps([1, 2, 3]), 2)],
)
def test_cli_rejects_unusable_input(tmp_path, contents, expected):
    path = tmp_path / "bad.json"
    path.write_text(contents, encoding="utf-8")
    assert main([str(path)]) == expected


def test_cli_reports_a_missing_file_without_traceback(tmp_path):
    assert main([str(tmp_path / "absent.json")]) == 2


def test_cite_does_not_import_the_proxy():
    # The benchmark is the neutral measurer; importing the proxy would make a
    # competing gateway's stack a prerequisite for running it. Check the import
    # graph, not the text: the module docstring names the proxy on purpose.
    import ast

    import pii_leak_benchmark.cite as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith("llm_shield_proxy")]
