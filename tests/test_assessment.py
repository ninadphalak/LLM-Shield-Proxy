from __future__ import annotations

import json

from llm_shield_proxy.assessment import load_assessment_records, run_assessment, write_assessment_report
from llm_shield_proxy.cli import assess_main


def test_assessment_is_aggregate_and_performs_no_network_calls(tmp_path, monkeypatch):
    source = tmp_path / "traffic.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps({"messages": [{"role": "user", "content": "Email alice@example.com"}]}),
                json.dumps({"input": "No protected information here."}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")

    report = run_assessment(str(source), enable_tier2=False, enable_tier3=False)

    serialized = json.dumps(report)
    assert report["source"]["records"] == 2
    assert report["findings"]["entity_counts"]["EMAIL"] == 1
    assert report["privacy"] == {
        "raw_records_persisted": False,
        "redacted_records_persisted": False,
        "network_calls_performed": False,
    }
    assert "alice@example.com" not in serialized
    assert "[EMAIL_1]" not in serialized
    assert report["oscal"]["assessment-results"]["import-ap"]["href"].startswith("urn:uuid:")


def test_assessment_report_writes_json_html_and_oscal_without_source_content(tmp_path):
    source = tmp_path / "traffic.json"
    source.write_text(json.dumps(["SSN 123-45-6789"]), encoding="utf-8")
    report = run_assessment(str(source), enable_tier2=False, enable_tier3=False)

    paths = write_assessment_report(report, str(tmp_path / "report"))

    assert set(paths) == {"json", "html", "oscal"}
    combined = "".join((tmp_path / "report" / name).read_text(encoding="utf-8") for name in [
        "assessment.json",
        "assessment.html",
        "oscal-assessment-results.json",
    ])
    assert "123-45-6789" not in combined
    assert "[SSN_1]" not in combined


def test_assessment_is_reproducible_with_source_date_epoch(tmp_path, monkeypatch):
    source = tmp_path / "traffic.json"
    source.write_text(json.dumps(["Email alice@example.com"]), encoding="utf-8")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")

    first = run_assessment(str(source), enable_tier2=False, enable_tier3=False)
    second = run_assessment(str(source), enable_tier2=False, enable_tier3=False)

    assert first == second


def test_load_assessment_records_rejects_scalar_numbers(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("42", encoding="utf-8")

    try:
        load_assessment_records(source)
    except ValueError as exc:
        assert "object or string" in str(exc)
    else:
        raise AssertionError("Expected scalar JSON input to be rejected")


def test_assess_cli_returns_nonzero_for_missing_source(tmp_path, capsys):
    exit_code = assess_main(["--input", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "out")])

    assert exit_code == 2
    assert "Assessment failed" in capsys.readouterr().err
