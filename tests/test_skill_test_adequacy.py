"""Check the pilot's oracle separately from the external scoring runner."""

import copy
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "benchmarks" / "skill_test_adequacy.py"
spec = importlib.util.spec_from_file_location("skill_test_adequacy", MODULE)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_baseline_and_benign_controls_satisfy_every_rule():
    assert pilot.violations(pilot.baseline()) == []
    controls = [c for c in pilot.cases() if c["kind"] == "benign"]
    assert len(controls) == 4
    for case in controls:
        assert pilot.violations(case["trace"]) == []


def test_twenty_defects_have_observed_witnesses_for_ten_rules():
    defects = [c for c in pilot.cases() if c["kind"] == "defect"]
    assert len(defects) == 20
    assert {c["rule"] for c in defects} == set(pilot.RULES)
    for case in defects:
        assert case["rule"] in pilot.violations(case["trace"]), case["id"]
        assert case["trace"]["success"] is True


def test_checker_does_not_trust_a_declared_pass():
    trace = pilot.baseline()
    trace["events"][3]["payload"] = pilot.CANARY
    trace["claimed_policy_pass"] = True
    assert "R03" in pilot.violations(trace)


def test_missing_ack_and_guarded_attempt_are_distinct():
    trace = pilot.baseline()
    trace["events"][3]["ack"] = False
    assert pilot.violations(trace) == ["R10"]
    trace["events"][3]["blocked"] = True
    trace["events"][3]["payload"] = pilot.CANARY
    trace["delivered"] = False
    assert pilot.violations(trace) == []
    assert pilot.attempted_violations(trace) == ["R03"]


def test_redaction_must_precede_the_outbound_effect():
    trace = pilot.baseline()
    trace["events"][2], trace["events"][3] = trace["events"][3], trace["events"][2]
    assert "R05" in pilot.violations(trace)


@pytest.mark.parametrize("size,invalid", [(1023, False), (1024, False), (1025, True)])
def test_upload_size_is_bytes_and_has_an_inclusive_boundary(size, invalid):
    trace = pilot.baseline()
    trace["events"][3]["payload"] = "x" * size
    assert ("R06" in pilot.violations(trace)) == invalid
    trace["events"][3]["payload"] = "\u00e9" * 513
    assert "R06" in pilot.violations(trace)


def test_oracle_is_pure_and_does_not_use_case_labels():
    trace = pilot.baseline()
    before = copy.deepcopy(trace)
    pilot.violations(trace)
    assert trace == before
    assert "kind" not in trace and "rule" not in trace


def test_unobserved_mutations_are_not_counted_as_proven_defects():
    unobserved = [c for c in pilot.cases() if c["kind"] == "unobserved"]
    assert len(unobserved) == 2
    assert all(not pilot.violations(c["trace"]) for c in unobserved)
