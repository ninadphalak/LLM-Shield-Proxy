"""Tests for the intake script: verifying numbers come from CI artifacts, not issue text."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "conformance-result.yml"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intake = _load("conformance_intake", "scripts/process_conformance_submission.py")
flags = _load("result_flags", "scripts/count_result_flags.py")


def _template_labels() -> list[str]:
    """Extract field labels from the issue form YAML."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return re.findall(r"^\s+label:\s*(.+?)\s*$", text, flags=re.MULTILINE)


# ------------------------------------------------------------- the three-way contract


def test_every_issue_form_field_has_a_parser_heading():
    missing = [
        label for label in _template_labels() if label.casefold() not in intake.FIELD_BY_HEADING
    ]
    assert not missing, f"The parser does not know these issue-form sections: {missing}"


def test_the_submit_command_writes_the_headings_the_parser_reads():
    from pii_leak_benchmark.submit import SUBMISSION_SECTIONS

    assert set(intake.FIELD_BY_HEADING) == {s.casefold() for s in SUBMISSION_SECTIONS}


def test_both_submission_paths_parse_to_the_same_fields():
    from urllib.parse import parse_qs, urlsplit

    from pii_leak_benchmark.submit import build_body, submission_url

    report = {"schema": "pii-leak-benchmark/operator-run/v1", "verdict": "LEAK",
              "target_version": "1.2.3", "contract": {"harness_version": "0.3.1"}}
    from_link = intake.parse_submission(
        parse_qs(urlsplit(submission_url(report)).query)["body"][0]
    )
    from_gh = intake.parse_submission(build_body(report, citation="x"))
    assert set(from_link) == set(from_gh)
    assert from_link["version"] == from_gh["version"] == "1.2.3"


def test_the_summary_link_and_the_command_agree():
    """Ensure `ci` and `submit` generate the same submission URL format."""
    from pii_leak_benchmark import ci
    from pii_leak_benchmark.submit import submission_url

    run = {"schema": "pii-leak-benchmark/operator-run/v1",
           "contract": {"profile": "p", "duty": "restore", "seed": "a"},
           "verdict": "CLEAN", "reason": "r", "target_version": "9", "entities": {},
           "required_checks": {}, "coverage": []}
    assert submission_url(run) in ci.render_summary(run)


# ------------------------------------------------------------------------ parsing


def test_parsing_survives_crlf_reordering_and_an_unknown_section():
    body = (
        "### Outcome\r\n\r\nCLEAN\r\n\r\n"
        "### Something we never asked for\r\n\r\nignore me\r\n\r\n"
        "### Gateway\r\n\r\nsome-gateway\r\n"
    )
    fields = intake.parse_submission(body)
    assert fields["outcome"] == "CLEAN"
    assert fields["gateway"] == "some-gateway"
    assert "ignore me" not in json.dumps(fields)


def test_an_unfilled_optional_section_reads_as_empty():
    fields = intake.parse_submission("### Notes\n\n_No response_\n\n### Project link\n\nN/A\n")
    assert fields["notes"] == ""
    assert fields["project_url"] == ""


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("holds back a short tail (held-tail)", "held-tail"),
        ("checks each chunk alone (per-chunk)", "per-chunk"),
        ("buffered", "buffered"),
        ("not stated", "not-stated"),
        ("whatever it feels like", None),
    ],
)
def test_architecture_answers_map_onto_the_published_union(answer, expected):
    assert intake.normalize_architecture(answer) == expected


def test_every_dropdown_option_in_the_form_maps_to_a_union_value():
    text = TEMPLATE.read_text(encoding="utf-8")
    block = text.split("label: How it reads the stream", 1)[1].split("validations", 1)[0]
    options = re.findall(r"^\s+- (.+?)\s*$", block, flags=re.MULTILINE)
    assert options
    assert all(intake.normalize_architecture(option) for option in options)


# --------------------------------------------------------------------- provenance


def _run_payload(*, branch="feature", fork=False, head="o/r", home="o/r", default="main"):
    """Mock a run payload, deliberately omitting `default_branch` to match the real API."""
    return {
        "head_branch": branch,
        "head_repository": {"full_name": head, "fork": fork},
        "repository": {"full_name": home},
    }


def _fetch(payload, default="main"):
    def fetch(url):
        if url.endswith("/actions/runs/42"):
            return payload
        return {"default_branch": default}

    return fetch


@pytest.mark.parametrize(
    "payload,expected",
    [
        (_run_payload(branch="main"), "submitted-main"),
        (_run_payload(branch="feature"), "submitted-branch"),
        (_run_payload(fork=True, head="someone/r"), "submitted-fork"),
        (_run_payload(head="someone/r"), "submitted-fork"),
        (_run_payload(branch=""), "submitted-unverified"),
    ],
)
def test_provenance_follows_the_run_not_the_submitter(payload, expected):
    value, reason, where = intake.classify_provenance(
        "https://github.com/o/r/actions/runs/42", fetch=_fetch(payload)
    )
    assert value == expected
    assert reason
    assert where == ("o", "r", "42")


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        urllib.error.URLError("timed out"),
        ValueError("not json"),
    ],
)
def test_an_unreadable_run_claims_the_least_rather_than_failing(failure):
    def fetch(url):
        raise failure

    value, reason, _ = intake.classify_provenance("https://github.com/o/r/actions/runs/42", fetch=fetch)
    assert value == "submitted-unverified"
    assert reason


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://evil.example/o/r/actions/runs/42",
        "https://github.com.evil.example/o/r/actions/runs/42",
        "https://github.com/o/r/pull/42",
        "javascript:alert(1)",
    ],
)
def test_a_link_that_is_not_a_github_run_is_never_trusted(url):
    called = []
    value, _, where = intake.classify_provenance(url, fetch=lambda u: called.append(u))
    assert value == "submitted-unverified"
    assert where is None
    assert not called, "A URL that failed the shape check must not be fetched"


def test_a_submitted_row_can_never_claim_to_have_been_measured_here():
    source = (REPO_ROOT / "scripts" / "process_conformance_submission.py").read_text(encoding="utf-8")
    assert '"measured-here"' not in source and "'measured-here'" not in source


# ------------------------------------------------------------- reading the artifact


def _zip_of(files: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content if isinstance(content, str) else json.dumps(content))
    return buffer.getvalue()


def _operator_run(entities=None, fidelity=False):
    return {
        "schema": "pii-leak-benchmark/operator-run/v1",
        "verdict": "LEAK",
        "entities": entities or {"EMAIL": "leak", "SSN": "contained", "CREDIT_CARD": "contained"},
        "required_checks": {"response_fidelity": fidelity},
        "contract": {"harness_version": "0.3.1"},
    }


def _raw_report(fidelity=False):
    return {"schema": "pii-leak-benchmark/http-profile/v1",
            "checks": {"response_fidelity": {"passed": fidelity},
                       "configured_upstream_boundary": {"leaked_entity_types": ["EMAIL"]}}}


def _split_report(single=0.125, adversarial=1.0):
    return {
        "schema": "pii-leak-benchmark/http-profile/v2",
        "metrics": {
            "leak_rate": {"single_chunk": single, "adversarial": adversarial},
            "cases_by_condition": {"single_chunk": 16, "adversarial": 16},
            "fidelity_rate": 1.0,
        },
    }


def test_reports_are_read_out_of_the_artifact_zip():
    found = intake.reports_from_zip(
        _zip_of({"reports/current.json": _operator_run(), "reports/summary.md": "not json"})
    )
    assert "current.json" in found
    assert "summary.md" not in found


def test_an_unreadable_member_does_not_lose_the_rest():
    found = intake.reports_from_zip(_zip_of({"a.json": "{ broken", "current.json": _operator_run()}))
    assert list(found) == ["current.json"]


def test_an_oversized_member_is_skipped(monkeypatch):
    monkeypatch.setattr(intake, "MAX_MEMBER_BYTES", 10)
    assert intake.reports_from_zip(_zip_of({"current.json": _operator_run()})) == {}


def test_the_first_artifact_holding_a_report_is_used():
    def api(url):
        return {"artifacts": [{"id": 1, "name": "logs", "expired": False},
                              {"id": 2, "name": "pii-leak-benchmark", "expired": False}]}

    def download(owner, repo, artifact_id):
        return _zip_of({"x.json": {"unrelated": True}} if artifact_id == 1
                       else {"current.json": _operator_run()})

    reports, evidence = intake.collect_reports("o", "r", "42", api=api, download=download)
    assert "current.json" in reports
    assert "pii-leak-benchmark" in evidence


def test_combined_source_artifact_precedes_an_operator_only_artifact():
    def api(url):
        return {"artifacts": [{"id": 1, "name": "operator-profile", "expired": False},
                              {"id": 2, "name": "source-reproduction", "expired": False}]}

    def download(owner, repo, artifact_id):
        if artifact_id == 1:
            return _zip_of({"current.json": _operator_run()})
        return _zip_of({"current.json": _operator_run(),
                        "source-response-on.json": _split_report(0.0, 0.0),
                        "source-response-off.json": _split_report(1.0, 1.0)})

    reports, evidence = intake.collect_reports("o", "r", "42", api=api, download=download)
    assert "source-response-on.json" in reports
    assert "source-reproduction" in evidence


@pytest.mark.parametrize(
    "api,download,expected",
    [
        (lambda u: (_ for _ in ()).throw(urllib.error.URLError("no")), None, "could not be listed"),
        (lambda u: {"artifacts": []}, None, "no artifacts"),
        (lambda u: {"artifacts": [{"id": 1, "name": "a", "expired": True}]}, None, "expired"),
    ],
)
def test_every_artifact_failure_ends_as_no_report_and_never_an_exception(api, download, expected):
    reports, evidence = intake.collect_reports(
        "o", "r", "42", api=api, download=download or (lambda *a: b"")
    )
    assert reports == {}
    assert expected in evidence


def test_a_download_that_fails_is_reported_not_raised():
    def download(owner, repo, artifact_id):
        raise RuntimeError("403")

    reports, evidence = intake.collect_reports(
        "o", "r", "42",
        api=lambda u: {"artifacts": [{"id": 1, "name": "a", "expired": False}]},
        download=download,
    )
    assert reports == {}
    assert "could not be read" in evidence


# -------------------------------------------------------------------- derivation


def test_what_reached_the_provider_is_read_from_the_entities():
    derived = intake.derive_measurements({"current.json": _operator_run()})
    assert derived["sentN"] == 1
    assert derived["sent"] == "email addresses"


def test_a_clean_run_says_none_rather_than_zero_types():
    derived = intake.derive_measurements(
        {"current.json": _operator_run({"EMAIL": "contained", "SSN": "contained"})}
    )
    assert derived["sent"] == "none"
    assert derived["sentN"] == 0


def test_every_type_leaking_is_said_as_all_of_them():
    derived = intake.derive_measurements(
        {"current.json": _operator_run({"EMAIL": "leak", "SSN": "leak", "CREDIT_CARD": "leak"})}
    )
    assert derived["sent"] == "all 3 types"


def test_a_type_that_was_not_measured_is_not_counted_as_contained():
    derived = intake.derive_measurements(
        {"current.json": _operator_run({"EMAIL": "leak", "SSN": "not measured"})}
    )
    assert derived["sentN"] == 1
    assert derived["_measured"] == ["EMAIL"]


def test_the_raw_report_settles_fidelity_when_the_operator_run_dropped_it():
    """Read fidelity from the raw report when the anonymize duty removes it from the operator run."""
    operator = _operator_run()
    del operator["required_checks"]["response_fidelity"]
    derived = intake.derive_measurements(
        {"current.json": operator, "current.raw.json": _raw_report(fidelity=True)}
    )
    assert derived["restored"] == "all"
    assert derived["restoredN"] == 1.0


def test_the_leak_columns_are_absent_without_a_response_split_report():
    derived = intake.derive_measurements({"current.json": _operator_run()})
    for field in ("leakWhole", "leakWholeN", "leakSplit", "leakSplitN"):
        assert field not in derived, "an unmeasured column must be absent, never zero"


def test_the_leak_columns_come_from_the_response_split_report_as_counts():
    derived = intake.derive_measurements(
        {"current.json": _operator_run(), "v2.json": _split_report(0.125, 1.0)}
    )
    assert derived["leakWhole"] == "2 of 16"
    assert derived["leakWholeN"] == 0.125
    assert derived["leakSplit"] == "16 of 16"


def _source_pair():
    on = _split_report(0.0, 0.0)
    off = _split_report(1.0, 1.0)
    for name, report in (("source-response-on", on), ("source-response-off", off)):
        report["schema"] = "llm-shield.streaming-privacy-http-profile/v2.0.0"
        report["implementation"] = {"name": f"external-gateway:{name}"}
        report["checks"] = {"configured_upstream_boundary": {"passed": True}}
        report["harness_revision"] = "0.2.1"
        report["cases_digest"] = "a" * 64
        report["corpus"] = {"seed": "a1b2c3d4e5f60001", "sha256": "a" * 64}
        report["instrument"] = {"inspector_sha256": "94262e29a492ab6a"}
        report["metrics"]["cases_scored"] = 32
        report["metrics"]["cases_inconclusive"] = 0
        report["metrics"]["partition_oracle"] = {"oracle": "midpoint"}
    return on, off


def test_source_pair_contract_accepts_the_retained_round_eight_reports():
    root = REPO_ROOT / "benchmarks/results/v2-response-split"
    on = json.loads((root / "llm-shield-proxy-1.6.6-response-on.json").read_text(encoding="utf-8"))
    off = json.loads((root / "llm-shield-proxy-1.6.6-response-off.json").read_text(encoding="utf-8"))
    assert intake.is_complete_source_pair(on, off)


def test_source_pair_scores_the_on_arm_even_when_off_appears_first():
    on, off = _source_pair()
    operator = _operator_run({"SLACK_TOKEN": "leak", "EMAIL": "contained"})
    operator["contract"]["harness_version"] = "0.4.1"
    derived = intake.derive_measurements({
        "source-response-off.json": off,
        "current.json": operator,
        "source-response-on.json": on,
        "source-identity.json": {"schema": "pii-leak-benchmark/source-build/v1",
                                 "source_commit": "a" * 40,
                                 "source_selector": "v1.6.6"},
    })
    assert derived["sent"] == "Slack tokens"
    assert derived["leakWhole"] == "0 of 16"
    assert derived["leakSplit"] == "0 of 16"
    assert "0.4.1" in derived["harness"] and "0.2.1" in derived["harness"]
    assert "source commit aaaaaaaa" in intake.write_note(derived)


def test_source_pair_needs_both_valid_arms_before_publishing_response_columns():
    on, off = _source_pair()
    for reports in (
        {"current.json": _operator_run(), "source-response-off.json": off},
        {"current.json": _operator_run(), "source-response-on.json": on},
        {"current.json": _operator_run(), "source-response-on.json": on,
         "source-response-off.json": {**off, "cases_digest": "b" * 64}},
        {"current.json": _operator_run(), "source-response-on.json": on,
         "source-response-off.json": {**off, "implementation": {"name": "external-gateway:source-response-on"}}},
        {"current.json": _operator_run(), "source-response-on.json": on,
         "source-response-off.json": {**off, "checks": {"configured_upstream_boundary": {"passed": False}}}},
        {"current.json": _operator_run(), "source-response-on.json": on,
         "source-response-off.json": {**off, "metrics": {**off["metrics"], "partition_oracle": {"oracle": "exhaustive-2-part"}}}},
        {"current.json": _operator_run(), "source-response-on.json": on,
         "source-response-off.json": {**off, "metrics": {**off["metrics"], "cases_inconclusive": 1}}},
    ):
        derived = intake.derive_measurements(reports)
        assert "leakWhole" not in derived
        assert "leakSplit" not in derived


def test_nothing_is_derived_from_an_empty_artifact():
    assert intake.derive_measurements({}) == {}


def test_not_measured_operator_does_not_become_a_no_leak_wall_claim():
    operator = _operator_run()
    operator["verdict"] = "NOT MEASURED"
    operator["entities"] = {}
    derived = intake.derive_measurements({"current.json": operator})
    assert "sent" not in derived
    assert "restored" not in derived


@pytest.mark.parametrize(
    "reports,expected",
    [
        ({"current.json": _operator_run({"EMAIL": "contained"}, fidelity=True)},
         "Kept everything out of the provider request. Handed every value back to the client."),
        ({"current.json": _operator_run()}, "Sent email addresses to the provider."),
    ],
)
def test_the_note_only_says_what_the_run_recorded(reports, expected):
    assert intake.write_note(intake.derive_measurements(reports)).startswith(expected.split(".")[0])


def test_the_note_mentions_splitting_only_when_splitting_was_measured():
    without = intake.write_note(intake.derive_measurements({"current.json": _operator_run()}))
    with_split = intake.write_note(
        intake.derive_measurements({"current.json": _operator_run(), "v2.json": _split_report()})
    )
    assert "Splitting" not in without
    assert "Splitting" in with_split


# ------------------------------------------------------------------- the published row


def _good_fields(**overrides):
    fields = {
        "gateway": "some-gateway",
        "version": "1.2.3, default settings",
        "architecture": "buffered",
        "license": "Apache-2.0",
        "run_url": "https://github.com/o/r/actions/runs/42",
    }
    fields.update(overrides)
    return fields


def _row(reports=None, provenance="submitted-main"):
    return intake.build_row(
        _good_fields(),
        provenance,
        intake.derive_measurements(reports if reports is not None else {"current.json": _operator_run()}),
        issue_number=7, submitter="someone", date="2026-09-19", evidence="Read from the artifact.",
    )


def test_a_row_built_from_an_artifact_is_published():
    row = _row()
    assert row["status"] == "published"
    assert row["sent"] == "email addresses"
    assert row["note"]


def test_a_row_with_no_report_stays_a_draft():
    row = _row(reports={})
    assert row["status"] == "draft"
    assert "sent" not in row


def test_a_published_row_never_carries_an_unmeasured_leak_column():
    row = _row()
    assert "leakWholeN" not in row and "leakSplitN" not in row


def test_a_row_round_trips_through_json_unchanged():
    row = _row()
    assert json.loads(json.dumps(row)) == row


def test_a_run_link_that_failed_its_shape_check_is_not_carried_into_the_row():
    row = intake.build_row(
        _good_fields(run_url="javascript:alert(1)"), "submitted-unverified",
        intake.derive_measurements({"current.json": _operator_run()}),
        issue_number=7, submitter="s", date="2026-09-19", evidence="",
    )
    assert "runUrl" not in row


def test_a_project_link_that_is_not_http_is_dropped():
    row = intake.build_row(
        _good_fields(project_url="javascript:alert(1)"), "submitted-main",
        intake.derive_measurements({"current.json": _operator_run()}),
        issue_number=7, submitter="s", date="2026-09-19", evidence="",
    )
    assert "pricingUrl" not in row


# --------------------------------------------------------------------- validation


def test_a_complete_submission_has_no_problems():
    assert intake.validate(_good_fields()) == []


def test_a_run_link_is_required_because_the_report_comes_from_it():
    assert any("run link" in p or "run_url" in p for p in intake.validate(_good_fields(run_url="")))


def test_a_run_link_of_the_wrong_shape_is_explained_rather_than_ignored():
    problems = intake.validate(_good_fields(run_url="https://example.com/whatever"))
    assert any("actions/runs" in problem for problem in problems)


def test_a_citation_block_is_no_longer_demanded():
    """The numbers come from the artifact now, so the paste is not load-bearing."""
    assert "citation" not in intake.REQUIRED_FIELDS
    assert intake.validate(_good_fields()) == []


def test_every_problem_is_reported_at_once():
    assert len(intake.validate({})) >= 5


# ------------------------------------------------------------------- sanitisation


@pytest.mark.parametrize(
    "hostile", ["a—b", "line\nbreak", "null\x00byte", "zero​width", "  padded  "]
)
def test_submitted_text_is_cleaned_before_it_can_be_published(hostile):
    cleaned = intake.clean(hostile)
    assert "—" not in cleaned
    assert "\n" not in cleaned and "\x00" not in cleaned and "​" not in cleaned
    assert cleaned == cleaned.strip()


def test_a_long_field_is_capped():
    assert len(intake.clean("x" * 5000, limit=40)) <= 40


def test_markup_is_carried_as_text_and_not_escaped_twice():
    assert intake.clean('</script><b>"x"</b>') == '</script><b>"x"</b>'


# ----------------------------------------------------------------------- writing


def test_a_resubmission_replaces_its_own_row_rather_than_adding_a_second(tmp_path):
    path = tmp_path / "submitted-rows.json"
    path.write_text('{"entries": []}', encoding="utf-8")
    intake.append_row(_row(), path=path)
    corrected = _row()
    corrected["version"] = "1.2.4"
    intake.append_row(corrected, path=path)
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 1
    assert entries[0]["version"] == "1.2.4"


def test_the_rows_file_in_the_tree_is_valid():
    document = json.loads(
        (REPO_ROOT / "website" / "src" / "data" / "submitted-rows.json").read_text(encoding="utf-8")
    )
    assert isinstance(document.get("entries"), list)
    assert all(entry.get("status") in ("draft", "published") for entry in document["entries"])


# ---------------------------------------------------------------------- the comment


def test_the_published_comment_says_where_the_numbers_came_from():
    text = intake.render_comment(_row(), "The run is on `main`.", "Read from the artifact.", [])
    assert "Published" in text
    assert "read out of that run's own artifact" in text
    assert "not that the gateway was the version named" in text


def test_the_published_comment_explains_an_unmeasured_column():
    text = intake.render_comment(_row(), "reason", "evidence", [])
    assert "not measured" in text


def test_a_row_with_every_column_does_not_explain_a_missing_one():
    row = _row(reports={"current.json": _operator_run(), "v2.json": _split_report()})
    text = intake.render_comment(row, "reason", "evidence", [])
    assert "response-split columns say" not in text


def test_the_unpublished_comment_asks_for_the_thing_that_was_missing():
    text = intake.render_comment(_row(reports={}), "reason", "No artifact.", [])
    assert "Not published yet" in text
    assert "current.json" in text


def test_the_failure_comment_names_every_problem():
    problems = intake.validate({})
    text = intake.render_comment({}, "", "", problems)
    assert all(problem in text for problem in problems)


# ------------------------------------------------------------------ dispute counts


def test_a_dispute_counts_against_every_row_it_names():
    counts = flags.count_by_issue([{"number": 90, "title": "Question: ", "body": "#7 and #8 are wrong"}])
    assert counts == {7: 1, 8: 1}


def test_an_issue_referring_to_itself_is_not_a_dispute_of_a_row():
    assert flags.count_by_issue([{"number": 90, "title": "x", "body": "see #90"}]) == {}


def test_a_bare_number_in_prose_is_not_a_reference():
    counts = flags.count_by_issue([{"number": 90, "title": "x", "body": "it leaked 16 of 16"}])
    assert counts == {}


def test_counts_are_written_onto_the_row_and_cleared_when_they_go():
    document = {"entries": [{"project": "a", "_submission": {"issue": 7}}]}
    assert flags.apply_counts(document, {7: 2}) == 1
    assert document["entries"][0]["flags"] == {"count": 2, "issue": 7}
    assert flags.apply_counts(document, {}) == 1
    assert "flags" not in document["entries"][0]


def test_an_unchanged_count_is_not_a_change():
    document = {"entries": [{"project": "a", "flags": {"count": 2, "issue": 7}, "_submission": {"issue": 7}}]}
    assert flags.apply_counts(document, {7: 2}) == 0


def test_an_archive_with_too_many_members_is_not_walked_to_the_end(monkeypatch):
    """Refuse to iterate a zip with too many members to prevent zip bombs."""
    monkeypatch.setattr(intake, "MAX_MEMBERS", 5)
    crowd = {f"f{n}.json": {"n": n} for n in range(50)}
    crowd["current.json"] = _operator_run()
    assert len(intake.reports_from_zip(_zip_of(crowd))) <= 5


def test_no_more_reports_are_parsed_than_a_run_could_have(monkeypatch):
    monkeypatch.setattr(intake, "MAX_REPORTS", 3)
    assert len(intake.reports_from_zip(_zip_of({f"f{n}.json": {"n": n} for n in range(40)}))) == 3


def test_a_courtesy_that_fails_does_not_fail_the_job(monkeypatch, capsys):
    """Failing to comment or label should not fail the job after a successful commit."""
    class Failed:
        returncode = 1
        stderr = "label 'needs-info' not found"
        stdout = ""

    monkeypatch.setattr(intake.subprocess, "run", lambda *a, **k: Failed())
    assert intake.label(7, "needs-info") is False
    assert intake.comment(7, "hello") is False
    assert intake.close_issue(7) is False
    assert "Could not" in capsys.readouterr().err


def test_a_default_branch_run_is_not_downgraded_when_the_run_payload_omits_it():
    """Ensure a main-branch run isn't downgraded due to GitHub API omitting `default_branch`."""
    value, reason, _ = intake.classify_provenance(
        "https://github.com/o/r/actions/runs/42", fetch=_fetch(_run_payload(branch="main"))
    )
    assert value == "submitted-main"
    assert "main" in reason


def test_an_unreadable_repository_lookup_does_not_invent_a_default_branch():
    def fetch(url):
        if url.endswith("/actions/runs/42"):
            return _run_payload(branch="main")
        raise urllib.error.URLError("no")

    value, _, _ = intake.classify_provenance("https://github.com/o/r/actions/runs/42", fetch=fetch)
    assert value == "submitted-branch"


def _fake_git(staged, calls):
    """Mock git to record calls and answer staged-files queries."""
    class Result:
        returncode = 0
        stdout = "\n".join(staged)
        stderr = ""

    def run(command, *args, **kwargs):
        calls.append(list(command))
        return Result()

    return run


def test_publishing_refuses_to_push_anything_but_the_rows_file(monkeypatch):
    """Fail publishing if any file besides the rows file is staged."""
    calls = []
    monkeypatch.setattr(
        intake.subprocess, "run",
        _fake_git(["website/src/data/submitted-rows.json", ".github/workflows/ci.yml"], calls),
    )
    with pytest.raises(RuntimeError, match="refusing to publish"):
        intake.publish(_row(), 7)
    assert not any("push" in call for call in calls), "nothing may be pushed after a refusal"


def test_publishing_lands_through_a_pull_request_rather_than_around_the_rule(monkeypatch):
    """Publish via PR instead of bypassing branch protection rules."""
    calls = []
    monkeypatch.setattr(
        intake.subprocess, "run", _fake_git(["website/src/data/submitted-rows.json"], calls)
    )
    intake.publish(_row(), 7)
    flat = [" ".join(call) for call in calls]
    assert any("HEAD:intake/issue-7" in line for line in flat), "the branch is pushed"
    assert any(line.startswith("gh pr create") for line in flat)
    assert not any("HEAD:main" in line for line in flat), "never straight at the protected branch"
    # Ensure --auto is used to defer to GitHub's check timing.
    merge = next(line for line in flat if line.startswith("gh pr merge"))
    assert "--auto" in merge and "--squash" in merge and "--delete-branch" in merge


# ------------------------------------------------------------------- flood resistance


def test_the_same_run_posted_from_many_issues_is_one_row(tmp_path):
    """Deduplicate runs posted across multiple issues by updating the existing row."""
    path = tmp_path / "rows.json"
    path.write_text('{"entries": []}', encoding="utf-8")
    for number in range(1, 26):
        row = _row()
        row["_submission"]["issue"] = number
        intake.append_row(row, path=path)
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 1
    assert entries[0]["_submission"]["issue"] == 25, "the latest posting wins"


def test_two_genuinely_different_runs_both_get_a_row(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text('{"entries": []}', encoding="utf-8")
    for run in ("42", "43"):
        row = _row()
        row["runUrl"] = f"https://github.com/o/r/actions/runs/{run}"
        intake.append_row(row, path=path)
    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 2


def test_a_row_without_a_run_is_identified_by_target_instead():
    without = dict(_row())
    without.pop("runUrl", None)
    assert intake.row_identity(without)[0] == "target"
    assert intake.row_identity(_row())[0] == "run"


def test_a_resubmission_of_the_same_target_replaces_it(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text('{"entries": []}', encoding="utf-8")
    for version in ("1.0", "1.0"):
        row = _row()
        row.pop("runUrl", None)
        row["version"] = version
        intake.append_row(row, path=path)
    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 1


def test_rows_are_counted_per_submitter():
    entries = [
        {"_submission": {"submitter": "a"}},
        {"_submission": {"submitter": "a"}},
        {"_submission": {"submitter": "b"}},
        "not a dict",
    ]
    assert intake.submissions_by(entries, "a") == 2
    assert intake.submissions_by(entries, "nobody") == 0


def test_the_repository_a_run_happened_in_is_recorded_beside_the_claimed_name():
    """Record the actual repository to defend against misattribution."""
    row = _row()
    assert row["_submission"]["ranIn"] == "o/r"


def test_a_failed_pull_request_takes_its_branch_back_down(monkeypatch):
    """Delete the PR branch if PR creation or auto-merge fails."""
    calls = []

    class Result:
        returncode = 0
        stdout = "website/src/data/submitted-rows.json"
        stderr = ""

    def run(command, *args, **kwargs):
        calls.append(list(command))
        # Check command tuple to ensure precise matching.
        if list(command[:3]) == ["gh", "pr", "create"]:
            raise intake.subprocess.CalledProcessError(1, "gh pr create")
        return Result()

    monkeypatch.setattr(intake.subprocess, "run", run)
    with pytest.raises(intake.subprocess.CalledProcessError):
        intake.publish(_row(), 7)
    flat = [" ".join(call) for call in calls]
    assert any("push origin --delete intake/issue-7" in line for line in flat)


@pytest.mark.parametrize(
    "rate,restored,number",
    [(1.0, "all", 1.0), (0.0, "none", 0.0), (0.75, "some", 0.75), (0.5, "some", 0.5)],
)
def test_a_partial_fidelity_rate_is_reported_as_partial(rate, restored, number):
    """A partial fidelity rate (e.g., 0.75) must report as 'some', not 'all'."""
    split = _split_report()
    split["metrics"]["fidelity_rate"] = rate
    derived = intake.derive_measurements({"v2.json": split})
    assert derived["restored"] == restored
    assert derived["restoredN"] == number


@pytest.mark.parametrize("rate", [-0.1, 1.5, True, "1.0", None])
def test_a_fidelity_rate_that_is_not_a_rate_asserts_nothing(rate):
    split = _split_report()
    split["metrics"]["fidelity_rate"] = rate
    derived = intake.derive_measurements({"v2.json": split})
    assert "restored" not in derived


def test_a_boolean_check_still_wins_over_the_rate():
    """A boolean fidelity check overrides an absent or malformed rate."""
    derived = intake.derive_measurements(
        {"current.raw.json": _raw_report(fidelity=True), "v2.json": _split_report()}
    )
    assert derived["restored"] == "all"


def test_an_artifact_larger_than_the_cap_is_never_fetched():
    """Refuse oversized artifacts based on the API listing before downloading."""
    fetched = []

    def download(owner, repo, artifact_id):
        fetched.append(artifact_id)
        return b""

    reports, evidence = intake.collect_reports(
        "o", "r", "42",
        api=lambda u: {"artifacts": [
            {"id": 1, "name": "huge", "expired": False,
             "size_in_bytes": intake.MAX_ARTIFACT_BYTES + 1},
        ]},
        download=download,
    )
    assert reports == {}
    assert not fetched, "an oversized artifact must not be downloaded at all"
    assert "larger than" in evidence


def test_the_row_content_is_checked_before_anything_is_committed(monkeypatch):
    """Ensure the style scan runs before commit, as CI no longer checks row-only PRs."""
    calls = []
    monkeypatch.setattr(
        intake.subprocess, "run",
        lambda command, *a, **k: calls.append(list(command)) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    intake.check_row_content()
    flat = " ".join(calls[0])
    assert "pytest" in flat and "test_public_docs_style.py" in flat


def test_the_ci_filter_and_the_content_check_agree():
    """Verify that `ci.yml` skipping row-only PRs is paired with local content checking."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    script = (REPO_ROOT / "scripts" / "process_conformance_submission.py").read_text(encoding="utf-8")
    if "submitted-rows.json" in workflow and "paths-ignore" in workflow:
        assert "test_public_docs_style.py" in script, (
            "ci.yml skips row-only changes, so the intake must run the content scan itself"
        )


def test_the_harness_version_is_recorded_on_the_row():
    """Record the harness version used for the measurement."""
    derived = intake.derive_measurements({"current.json": _operator_run()})
    assert derived["harness"] == "0.3.1"


def test_the_research_spelling_of_the_harness_version_is_read_too():
    split = _split_report()
    split["harness_revision"] = "0.4.0"
    assert intake.derive_measurements({"v2.json": split})["harness"] == "0.4.0"


def test_a_report_with_no_harness_version_records_none():
    assert "harness" not in intake.derive_measurements({"v2.json": _split_report()})
