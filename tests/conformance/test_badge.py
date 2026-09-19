"""`pii-leak-benchmark badge` renders a report as a Shields.io endpoint file.

The subcommand exists so a README badge carries the MEASUREMENT rather than the
workflow's exit status. The risk it introduces is the mirror image: a badge is read at
a glance by people who will never open the report, so a green one on a run that was
never a verdict is the most misleading thing this package could emit. These tests pin
the colour rules, the three grey outcomes in particular, and the self-reported marker.
"""

from __future__ import annotations

import json

import pytest
from pii_leak_benchmark.badge import DEFAULT_LABEL, build_badge, main


def _report(**overrides):
    report = {
        "schema": "llm-shield.streaming-privacy-http-profile/v2.0.0",
        "harness_revision": "0.2.1",
        "generated_at": "2026-09-19T05:29:08Z",
        "outcome": "pass",
        "implementation": {"name": "llm-shield-proxy", "version": "1.6.6"},
        "metrics": {
            "leak_rate": {"single_chunk": 0.0, "adversarial": 0.0, "overall": 0.0},
            "cases_applicable": 32,
        },
    }
    report.update(overrides)
    return report


def test_the_payload_is_a_valid_shields_endpoint():
    badge = build_badge(_report())
    assert badge["schemaVersion"] == 1
    assert badge["label"] == DEFAULT_LABEL
    assert isinstance(badge["message"], str) and badge["message"]
    assert isinstance(badge["color"], str) and badge["color"]


def test_a_pass_is_the_only_green():
    assert build_badge(_report(outcome="pass"))["color"] == "brightgreen"
    for outcome in (
        "fail",
        "inconclusive",
        "redaction-not-enabled",
        "claim-unstated",
        "not-applicable",
        "no-leak-profile-not-met",
    ):
        colour = build_badge(_report(outcome=outcome))["color"]
        assert colour != "brightgreen", f"{outcome} rendered green"


@pytest.mark.parametrize(
    "outcome",
    ["inconclusive", "redaction-not-enabled", "claim-unstated"],
)
def test_a_run_that_is_not_a_verdict_reads_grey_and_says_so(outcome):
    """These three are the ones a reader is most likely to misread as "it held".

    None of them measured a clean gateway. A run with redaction switched off learned
    nothing about the product, and a run whose claim was never stated is not a verdict
    at all, so neither may borrow the vocabulary of a pass.
    """
    badge = build_badge(_report(outcome=outcome))
    assert badge["color"] == "lightgrey"
    assert "contained" not in badge["message"]
    assert "leaked" not in badge["message"]


def test_a_failing_run_says_how_many_cases_leaked():
    badge = build_badge(
        _report(
            outcome="fail",
            metrics={
                "leak_rate": {"single_chunk": 0.125, "adversarial": 0.25, "overall": 0.1875},
                "cases_applicable": 32,
            },
        )
    )
    assert badge["message"] == "leaked 6 of 32"
    assert badge["color"] == "critical"


def test_an_operator_run_names_the_types_that_crossed_the_boundary():
    """Operator runs do not partition into scored cases, but they do know which types
    reached the capture, and that is more use to a maintainer than a bare verdict."""
    badge = build_badge(
        _report(
            outcome="fail",
            metrics={},
            checks={
                "configured_upstream_boundary": {
                    "leaked_entity_types": ["SSN", "EMAIL"],
                }
            },
        )
    )
    assert badge["message"] == "leaked: EMAIL, SSN"


def test_a_pass_never_borrows_a_leak_count():
    """A stale or unrelated count on a passing report must not become the message."""
    badge = build_badge(
        _report(
            outcome="pass",
            checks={"configured_upstream_boundary": {"leaked_entity_types": ["EMAIL"]}},
        )
    )
    assert badge["message"] == "contained"


def test_a_failing_run_with_no_countable_detail_still_renders():
    badge = build_badge(_report(outcome="fail", metrics={}))
    assert badge["message"] == "leaked"
    assert badge["color"] == "critical"


def test_an_unrecognised_outcome_is_never_green():
    badge = build_badge(_report(outcome="something-new"))
    assert badge["color"] == "lightgrey"
    assert badge["message"] == "unknown outcome"


def test_a_missing_outcome_is_never_green():
    report = _report()
    del report["outcome"]
    badge = build_badge(report)
    assert badge["color"] == "lightgrey"


def test_the_published_file_records_that_nobody_verified_it():
    """The badge and its JSON are both served by the submitter. A reader who opens the
    file must find that stated, not inferred."""
    block = build_badge(_report())["pii_leak_benchmark"]
    assert block["verification"] == "self-reported"
    assert block["harness_revision"] == "0.2.1"
    assert block["target"] == "llm-shield-proxy"
    assert block["target_version"] == "1.6.6"


def test_the_label_is_overridable():
    badge = build_badge(_report(), label="gateway privacy")
    assert badge["label"] == "gateway privacy"


def test_main_writes_a_file_shields_can_read(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    out = tmp_path / "badge.json"

    assert main([str(report_path), "--out", str(out)]) == 0

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schemaVersion"] == 1
    assert written["message"] == "contained"


def test_main_reports_a_missing_report_rather_than_raising(tmp_path):
    assert main([str(tmp_path / "absent.json")]) == 2


def test_main_reports_invalid_json_rather_than_raising(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main([str(broken)]) == 2


def test_main_rejects_a_report_that_is_not_an_object(tmp_path):
    listy = tmp_path / "list.json"
    listy.write_text("[]", encoding="utf-8")
    assert main([str(listy)]) == 2


# --- The operator run shape ------------------------------------------------------
#
# `ci.py` writes `current.json` with a `verdict`, and records no redaction claim. That
# makes `outcome` derive as `claim-unstated` on every CI run, so a badge keyed on
# `outcome` was grey even when the run had measured a leak. CI is the only place the
# badge is produced automatically, so the feature could never once show what it exists
# to show. These pin the verdict path.


def _operator(**overrides):
    run = {
        "schema": "pii-leak-benchmark/operator-run/v1",
        "verdict": "LEAK",
        "reason": "Synthetic values were observed upstream.",
        "target_version": "1.2.3",
        "generated_at": "2026-09-19T05:29:08Z",
        "entities": {"EMAIL": "leak", "SSN": "clean", "CARDPAN": "leak"},
    }
    run.update(overrides)
    return run


def test_an_operator_leak_is_red_even_though_its_outcome_would_be_claim_unstated():
    """The regression this exists for: an operator run carries no redaction claim, so
    `outcome` derives as `claim-unstated`. Keying on it painted a measured leak grey."""
    badge = build_badge(_operator(outcome="claim-unstated"))
    assert badge["color"] == "critical"
    assert badge["message"] == "leaked: CARDPAN, EMAIL"


def test_a_clean_operator_run_is_green():
    badge = build_badge(
        _operator(verdict="CLEAN", entities={"EMAIL": "clean"}, outcome="claim-unstated")
    )
    assert badge["color"] == "brightgreen"
    assert badge["message"] == "contained"


@pytest.mark.parametrize(
    "verdict,colour",
    [("CHECK FAILED", "yellow"), ("NOT MEASURED", "lightgrey")],
)
def test_the_other_operator_verdicts_are_never_green(verdict, colour):
    badge = build_badge(_operator(verdict=verdict, outcome="claim-unstated"))
    assert badge["color"] == colour


def test_the_verdict_wins_over_a_contradictory_outcome():
    """Both fields present and disagreeing: the verdict is the one about the gateway."""
    badge = build_badge(_operator(verdict="LEAK", outcome="pass"))
    assert badge["color"] == "critical"


def test_an_unrecognised_verdict_is_never_green():
    badge = build_badge(_operator(verdict="SOMETHING ELSE"))
    assert badge["color"] == "lightgrey"


def test_the_recorded_result_prefers_the_verdict():
    block = build_badge(_operator())["pii_leak_benchmark"]
    assert block["result"] == "LEAK"


def test_an_operator_run_records_its_harness_and_target():
    """The two report shapes file the same facts under different names. Reading only the
    research one filled every CI badge's metadata with `unrecorded` while the report was
    carrying the answers."""
    block = build_badge(
        _operator(
            contract={
                "harness_version": "0.3.1",
                "instrument_sha256": "abc123",
                "model": "gateway-under-test",
            },
            target_version="2.0.0",
        )
    )["pii_leak_benchmark"]

    assert block["harness_revision"] == "0.3.1"
    assert block["target"] == "gateway-under-test"
    assert block["target_version"] == "2.0.0"
    assert block["instrument_sha256"] == "abc123"


def test_a_research_report_still_records_its_own_field_names():
    block = build_badge(_report())["pii_leak_benchmark"]
    assert block["harness_revision"] == "0.2.1"
    assert block["target"] == "llm-shield-proxy"
    assert block["target_version"] == "1.6.6"
    # No operator contract, so no scorer digest is invented for it.
    assert "instrument_sha256" not in block


def test_provenance_is_always_marked_self_reported():
    for report in (_report(), _operator()):
        assert build_badge(report)["pii_leak_benchmark"]["verification"] == "self-reported"
