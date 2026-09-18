"""A leak rate is a number. This file pins the output that makes it a bug ticket.

`leak_rate: 0.125` is correct, precise, and gives the person reading it no next action.
It does not say WHAT left, WHICH side of the gateway saw it, or WHAT to change. The
explanation layer in `pii_leak_benchmark.explain` replaces the bare number with three
things, and each of the three is pinned here because each can be quietly destroyed:

1.  **Two lines naming who saw what.** In a leak the two values are IDENTICAL, and that
    identity IS the finding: the gateway was supposed to change one of them and did not.
    A display that shows "sent" beside "received" without saying who is who therefore
    explains nothing -- it looks like a successful round trip. The labels carry the whole
    meaning, so `test_each_failure_kind_names_who_saw_what` fails if two kinds ever share
    a label pair.

2.  **One canned sentence.** Fixed strings chosen by a key, never generated. The failure
    mode is a table whose rows collapse onto each other as it grows, at which point the
    sentence stops distinguishing anything and the reader learns nothing from it;
    `test_no_two_canned_explanations_are_the_same` fails on the row that collapses.

3.  **The privacy rule, which is a rule about the ARTIFACT and not about the terminal.**
    The operator generated these values on their own machine seconds ago, so showing them
    their own test data costs nothing. Publishing it hands a specimen to everyone who
    reads the report. `values_published: false` has been a convention enforced by nobody,
    and a convention alone will be broken later -- so the published renderer is a pure
    function of the case identity here, and two tests scan real artifacts for real
    specimen values.

`test_the_inspector_digest_is_unmoved` is the load-bearing one. The IEEE evidence is
pinned to inspector `94262e29a492ab6a`; a pure output layer cannot move it, and this
test is what makes "cannot" true rather than intended.
"""

from __future__ import annotations

import json
import re
import socket
from typing import Any

import pytest
from pii_leak_benchmark import explain

SPECIMEN_EMAIL = "nuwpcbba@example.com"
SPECIMEN_SSN = "907-31-4459"
SPECIMEN_CARD = "4111-1111-1111-1111"


def _finding(**kwargs: Any) -> explain.Finding:
    defaults: dict[str, Any] = {
        "kind": explain.KIND_LEAK_TO_PROVIDER,
        "entity_type": "EMAIL",
        "carrier": "chat-content",
        "sent": SPECIMEN_EMAIL,
        "received": SPECIMEN_EMAIL,
        "seed": "a1b2c3d4",
    }
    defaults.update(kwargs)
    return explain.Finding(**defaults)


# ---------------------------------------------------------------- the digest is unmoved


def test_the_inspector_digest_is_unmoved() -> None:
    """The one number this whole change is not allowed to touch.

    Every figure in the IEEE submission's two tables comes from round-8 reports carrying
    inspector `94262e29a492ab6a`. The digest is scoped to the 34 scorers in
    `_INSTRUMENTED` plus three normalization helpers in `http_profile`, so an output layer
    CANNOT move it -- but "cannot" is a claim about where the code went, and code moves.
    If this fails, something that decides a measurement was edited, and the round-8
    evidence went stale the moment it did.
    """
    from pii_leak_benchmark.v2_emitter import inspector_digest

    assert inspector_digest() == "94262e29a492ab6a"


# ------------------------------------------------------------------ who saw what


def test_each_failure_kind_names_who_saw_what() -> None:
    """Three different failures, three different pairs of people. Never the same pair.

    "sent" beside "received" is the display that explains nothing: two identical values
    look like a successful round trip. What makes a leak legible is that one side is the
    caller and the other is a party who should never have had it -- so the labels are the
    finding, and no two kinds may share them.
    """
    seen: dict[tuple[str, str], str] = {}
    for kind in explain.KINDS:
        labels = explain.labels_for(kind)
        assert labels.sent and labels.received
        assert (labels.sent, labels.received) not in seen, (
            f"{kind} reuses the labels of {seen.get((labels.sent, labels.received))}"
        )
        seen[(labels.sent, labels.received)] = kind


def test_the_wrong_line_is_the_one_with_the_arrow() -> None:
    """Exactly one of the two lines is marked, and it is the one that should have differed."""
    for kind in explain.KINDS:
        block = "\n".join(explain.render(_finding(kind=kind), reveal=True))
        assert block.count("<-") == 1, block
        arrowed = next(line for line in block.splitlines() if "<-" in line)
        assert explain.labels_for(kind).received in arrowed, block


def test_a_leak_shows_the_same_value_twice_because_that_is_the_finding() -> None:
    """The identity of the two values is the whole point, so both lines must be printed.

    Collapsing them to one line ("EMAIL leaked: nuwpcbba@example.com") loses the fact
    being reported: the gateway had the value, was asked to change it, and returned it
    byte for byte.
    """
    block = "\n".join(explain.render(_finding(), reveal=True))
    assert block.count(SPECIMEN_EMAIL) == 2, block


def test_a_fidelity_failure_is_not_reported_as_a_leak() -> None:
    """Nothing escaped. Saying LEAK here sends an operator to fix the wrong subsystem."""
    block = "\n".join(
        explain.render(
            _finding(kind=explain.KIND_FIDELITY, received="[EMAIL_1]"), reveal=True
        )
    )
    assert block.startswith("FAIL")
    assert "LEAK" not in block
    assert "opposite" in block or "not a leak" in block.lower()


def test_a_value_that_never_came_back_says_so_rather_than_printing_a_blank() -> None:
    """An empty right-hand side reads as a formatting bug, not as a finding."""
    block = "\n".join(
        explain.render(_finding(kind=explain.KIND_FIDELITY, received=""), reveal=True)
    )
    assert "nothing" in block.lower()


# ------------------------------------------------------------------ the canned sentences


def test_no_two_canned_explanations_are_the_same() -> None:
    """The table's only job is to distinguish cases. Duplicate rows do not do that.

    Written as a sweep over the whole key space rather than a spot check, because the row
    that collapses is always the one added last and never the one anybody thought to
    assert on.
    """
    rendered: dict[str, tuple[Any, ...]] = {}
    for kind in explain.KINDS:
        for entity in explain.KNOWN_ENTITY_TYPES:
            for fragmented in (False, True):
                for configured in (False, True):
                    key = (kind, entity, fragmented, configured)
                    sentence = explain.explanation(
                        kind, entity, fragmented=fragmented,
                        redaction_configured=configured,
                    )
                    assert sentence, key
                    # A recorded claim only ADDS a clause to a provider leak. Everywhere
                    # else the two values of that axis are legitimately the same sentence,
                    # so they are keyed together rather than asserted apart.
                    if configured and kind != explain.KIND_LEAK_TO_PROVIDER:
                        continue
                    assert sentence not in rendered, (
                        f"{key} says exactly what {rendered[sentence]} says"
                    )
                    rendered[sentence] = key


def test_every_explanation_names_the_data_type_in_words_a_stranger_reads() -> None:
    """`CREDIT_CARD` is a corpus label. "payment card number" is what a reader knows."""
    for entity in explain.KNOWN_ENTITY_TYPES:
        phrase = explain.entity_phrase(entity)
        assert phrase and phrase != entity and "_" not in phrase
        sentence = explain.explanation(
            explain.KIND_LEAK_TO_PROVIDER, entity, fragmented=False
        )
        assert phrase in sentence, (entity, sentence)


def test_a_fragmented_finding_says_that_no_single_fragment_matched() -> None:
    """Otherwise the reader concludes the detector is simply broken, and rewrites it.

    A split value is a different defect from a missed value: every fragment was correctly
    judged harmless on its own. The fix is retention across events, not a better pattern.
    """
    whole = explain.explanation(explain.KIND_LEAK_TO_PROVIDER, "EMAIL", fragmented=False)
    split = explain.explanation(explain.KIND_LEAK_TO_PROVIDER, "EMAIL", fragmented=True)
    assert whole != split
    assert "join" in split.lower()


def test_a_finding_with_no_entity_still_reads_as_a_sentence() -> None:
    """The CI path hits this on every fidelity failure, and it produced a broken sentence.

    `ci` builds findings WITHOUT specimens on purpose, so it cannot ask which entity failed
    to come back and reports the check once with no entity. `entity_phrase("")` returned
    the empty string, so the published summary read "The caller's own was not returned."
    -- in a pull-request comment, as the tool's entire explanation of the failure.
    """
    report = {"checks": {"configured_upstream_boundary": {},
                         "response_fidelity": {"passed": False}}}
    finding = explain.findings_from_report(report, seed="s1")[0]
    assert finding.entity_type == ""

    sentence = " ".join(
        " ".join(explain.render(finding, reveal=False)).split()
    )
    assert "own was not" not in sentence, sentence
    assert "caller's own protected value" in sentence


def test_an_unknown_entity_type_still_gets_a_usable_sentence() -> None:
    """The corpus grows. An unknown label must degrade, never raise or print an empty line."""
    sentence = explain.explanation(
        explain.KIND_LEAK_TO_PROVIDER, "IBAN_NUMBER", fragmented=False
    )
    assert sentence.strip()
    assert "iban number" in sentence.lower()


# ------------------------------------------------------------------ the privacy rule


def test_the_operator_terminal_shows_the_operator_their_own_values() -> None:
    """They generated these on their own machine seconds ago. Withholding them helps nobody.

    This is the half that persuades: a maintainer who watches their own gateway hand back
    an address it was never given does not need to be talked into believing the result.
    """
    block = "\n".join(explain.render(_finding(entity_type="SSN", sent=SPECIMEN_SSN,
                                              received=SPECIMEN_SSN), reveal=True))
    assert SPECIMEN_SSN in block


@pytest.mark.parametrize(
    ("entity", "specimen"),
    [("EMAIL", SPECIMEN_EMAIL), ("SSN", SPECIMEN_SSN), ("CREDIT_CARD", SPECIMEN_CARD)],
)
def test_the_published_render_carries_the_shape_and_never_the_value(
    entity: str, specimen: str
) -> None:
    """`bob@example.com` becomes `<EMAIL>`, and the row stays useful without a specimen."""
    finding = _finding(entity_type=entity, sent=specimen, received=specimen)
    block = "\n".join(explain.render(finding, reveal=False))
    assert specimen not in block
    assert f"<{entity}>" in block


def test_the_published_render_cannot_depend_on_the_values_at_all() -> None:
    """Stronger than scanning for today's specimen, and it is the assertion that lasts.

    A scan proves this run's value is absent. This proves no run's value can be present:
    change both specimens to anything at all and the published block is byte-identical,
    so there is no input a future edit could smuggle through it.
    """
    identity = {
        "kind": explain.KIND_LEAK_TO_PROVIDER,
        "entity_type": "EMAIL",
        "carrier": "chat-content",
        "seed": "a1b2c3d4",
    }
    first = explain.render(
        explain.Finding(sent=SPECIMEN_EMAIL, received=SPECIMEN_EMAIL, **identity),
        reveal=False,
    )
    second = explain.render(
        explain.Finding(
            sent="totally-different@elsewhere.invalid",
            received="\n".join(["SSN 001-02-0003"] * 3),
            **identity,
        ),
        reveal=False,
    )
    assert first == second


def test_the_published_render_still_says_the_two_sides_matched() -> None:
    """Shapes, not values -- but an identical PAIR of shapes is still the finding."""
    block = "\n".join(explain.render(_finding(entity_type="CREDIT_CARD"), reveal=False))
    assert block.count("<CREDIT_CARD>") == 2, block


def test_a_fidelity_failure_publishes_no_gateway_output_either() -> None:
    """What came back instead is the TARGET's text, which the harness cannot vouch for.

    A one-way anonymizer returns `[EMAIL_1]` and a broken one could return anything at
    all, including another caller's data. The published row describes the placeholder; it
    never reproduces it.
    """
    block = "\n".join(
        explain.render(
            _finding(kind=explain.KIND_FIDELITY, received="[EMAIL_1] from acct 4242"),
            reveal=False,
        )
    )
    assert "4242" not in block and "[EMAIL_1]" not in block


def test_the_arrow_never_accuses_a_configuration_the_run_did_not_record() -> None:
    """Found by RUNNING the documented floor, not by reading the template.

    "redaction was on and did not hold" is a claim about a product's configuration. On
    `capture://self` there is no gateway at all, so the run that MUST report LEAK -- the
    one an operator is told to do first, to prove their capture works -- was printing a
    false sentence about a product that was never in the path. The weaker note states only
    what was observed and is true in every case.
    """
    unrecorded = "\n".join(explain.render(_finding(), reveal=True))
    assert "redaction" not in unrecorded, unrecorded
    assert "nothing removed it on the way" in unrecorded

    # Joined on spaces, not newlines: the sentence is wrapped to 72 columns, so a phrase
    # asserted against the raw block passes or fails on where the wrap happened to land.
    recorded = " ".join(explain.render(_finding(redaction_configured=True), reveal=True))
    assert "redaction was on and did not hold" in recorded
    assert "recorded as switched on for this run" in " ".join(recorded.split())


def test_only_a_recorded_claim_earns_the_stronger_note() -> None:
    """The claim block is the record. No claim block, no accusation."""
    evidence = [{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    without = _boundary_report(leaked=evidence)
    assert explain.findings_from_report(without)[0].redaction_configured is False

    with_claim = _boundary_report(leaked=evidence)
    with_claim["redaction_claim"] = {"configured_for_this_run": True}
    assert explain.findings_from_report(with_claim)[0].redaction_configured is True


# ------------------------------------------------------------------ the case identity


def test_the_block_carries_the_case_and_the_seed_so_the_run_repeats() -> None:
    for reveal in (True, False):
        block = "\n".join(explain.render(_finding(fragmented=True), reveal=reveal))
        assert "chat-content" in block
        assert "split=yes" in block
        assert "a1b2c3d4" in block


def test_an_unseeded_run_says_so_rather_than_printing_an_empty_seed() -> None:
    """`Seed:` with nothing after it reads as a bug. It is a real and reportable state."""
    block = "\n".join(explain.render(_finding(seed=""), reveal=True))
    assert re.search(r"Seed:\s*\S", block), block


# ------------------------------------------------------------------ findings from a run


def _boundary_report(
    *,
    leaked: list[dict[str, str]] | None = None,
    fidelity_passed: bool = True,
) -> dict[str, Any]:
    evidence = leaked or []
    return {
        "checks": {
            "configured_upstream_boundary": {
                "captured_requests": 2,
                "correlated_requests": 2,
                "uninspectable_requests": 0,
                "unattributed_uninspectable_requests": 0,
                "leaked_entity_types": sorted({item["entity_type"] for item in evidence}),
                "unattributed_leaked_entity_types": [],
                "leak_evidence": evidence,
                "unattributed_leak_evidence": [],
            },
            "response_fidelity": {"passed": fidelity_passed},
        },
        "fixture": {"formats": {"EMAIL": "", "SSN": "", "CREDIT_CARD": ""}},
    }


def test_a_request_path_leak_becomes_a_provider_finding() -> None:
    report = _boundary_report(
        leaked=[{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    )
    findings = explain.findings_from_report(report)
    assert [f.kind for f in findings] == [explain.KIND_LEAK_TO_PROVIDER]
    assert findings[0].entity_type == "EMAIL"
    assert findings[0].fragmented is False


def test_a_normalized_match_is_reported_as_the_fragmented_case() -> None:
    """`match: normalized` means the value was recovered only after joining and stripping.

    That is precisely the split case, and it needs the split sentence: the detector was
    not wrong about any fragment it saw.
    """
    report = _boundary_report(
        leaked=[{"entity_type": "SSN", "channel": "body", "scope": "cross-request",
                 "match": "normalized"}]
    )
    findings = explain.findings_from_report(report)
    assert findings[0].fragmented is True


def test_an_unattributed_finding_never_says_the_gateway_sent_it() -> None:
    """The report splits these two lists apart on purpose. The display must keep them apart.

    A PUBLIC capture is reachable by anyone, so traffic arrives that is not the target's.
    `http_profile` records it in its own field precisely "so a reader is not told the
    target sent them" -- and then the display said "The gateway forwarded the caller's own
    email address", which is the accusation that separation exists to prevent. Anyone who
    knows the fixture and the capture URL could manufacture it.
    """
    report = _boundary_report()
    report["checks"]["configured_upstream_boundary"].update({
        "unattributed_leaked_entity_types": ["EMAIL"],
        "unattributed_leak_evidence": [
            {"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
             "match": "literal"}
        ],
    })
    finding = explain.findings_from_report(report)[0]
    assert finding.attributed is False

    block = " ".join(" ".join(explain.render(finding, reveal=False)).split())
    assert "The gateway forwarded" not in block
    assert "not carry this run" in block or "not attributed" in block.lower()
    # Still a finding, and still loud. Unattributed does not mean harmless.
    assert block.startswith("LEAK")


def test_an_attributed_and_an_unattributed_leak_do_not_read_the_same() -> None:
    evidence = {"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                "match": "literal"}
    mine = _boundary_report(leaked=[evidence])
    theirs = _boundary_report()
    theirs["checks"]["configured_upstream_boundary"].update({
        "unattributed_leaked_entity_types": ["EMAIL"],
        "unattributed_leak_evidence": [evidence],
    })
    assert explain.render(explain.findings_from_report(mine)[0], reveal=False) !=         explain.render(explain.findings_from_report(theirs)[0], reveal=False)


def test_a_failed_restoration_becomes_a_fidelity_finding_not_a_leak() -> None:
    report = _boundary_report(fidelity_passed=False)
    findings = explain.findings_from_report(report)
    assert findings and all(f.kind == explain.KIND_FIDELITY for f in findings)


def test_a_clean_run_produces_no_findings_and_therefore_prints_nothing() -> None:
    assert explain.findings_from_report(_boundary_report()) == []


def test_the_published_path_can_carry_the_seed_it_never_has_the_values_for() -> None:
    """The CI summary is the reader who most needs the seed and least has it.

    A pull-request comment saying a card number left the gateway is not actionable without
    the one string that reproduces it, and the seed is not in the report -- `fixture`
    publishes only whether the values vary. The caller that chose the seed passes it.
    """
    report = _boundary_report(
        leaked=[{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    )
    finding = explain.findings_from_report(report, seed="gateway-ci-v1")[0]
    assert finding.sent == "" and finding.seed == "gateway-ci-v1"
    assert "gateway-ci-v1" in "\n".join(explain.render(finding, reveal=False))


def test_findings_built_without_specimens_still_publish() -> None:
    """The published path never has the values and must not need them."""
    report = _boundary_report(
        leaked=[{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    )
    finding = explain.findings_from_report(report)[0]
    assert finding.sent == "" and finding.received == ""
    assert "<EMAIL>" in "\n".join(explain.render(finding, reveal=False))


def test_specimens_reach_the_finding_when_the_operator_supplies_them() -> None:
    report = _boundary_report(
        leaked=[{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    )
    specimens = explain.OperatorSpecimens(
        fixture={"EMAIL": SPECIMEN_EMAIL}, prompt=f"contact {SPECIMEN_EMAIL}", seed="s1"
    )
    finding = explain.findings_from_report(report, specimens)[0]
    assert finding.sent == SPECIMEN_EMAIL == finding.received
    assert finding.seed == "s1"


def test_a_fidelity_finding_reports_what_came_back_in_its_place() -> None:
    """"The user saw [EMAIL_1]" is the line that tells an operator which fix they need."""
    report = _boundary_report(fidelity_passed=False)
    specimens = explain.OperatorSpecimens(
        fixture={"EMAIL": SPECIMEN_EMAIL},
        prompt=f"contact {SPECIMEN_EMAIL}, thanks",
        client_text="contact [EMAIL_1], thanks",
    )
    finding = next(
        f for f in explain.findings_from_report(report, specimens)
        if f.entity_type == "EMAIL"
    )
    assert finding.sent == SPECIMEN_EMAIL
    assert finding.received == "[EMAIL_1]"


def test_a_value_the_gateway_restored_correctly_is_not_reported_as_missing() -> None:
    """A run can fail `response_fidelity` on framing alone while every value came back.

    Naming an entity that was restored perfectly sends the operator to audit a vault that
    is working, and costs the report its credibility on the entities that did fail.
    """
    report = _boundary_report(fidelity_passed=False)
    specimens = explain.OperatorSpecimens(
        fixture={"EMAIL": SPECIMEN_EMAIL, "SSN": SPECIMEN_SSN},
        prompt=f"contact {SPECIMEN_EMAIL}, SSN {SPECIMEN_SSN}",
        client_text=f"contact {SPECIMEN_EMAIL}, SSN [SSN_1]",
    )
    entities = {f.entity_type for f in explain.findings_from_report(report, specimens)}
    assert entities == {"SSN"}


# ------------------------------------------------------------------ the operator command


def test_selfcheck_prints_the_explanation_with_the_operators_own_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pii_leak_benchmark import selfcheck

    report = _boundary_report(
        leaked=[{"entity_type": "EMAIL", "channel": "body", "scope": "per-request",
                 "match": "literal"}]
    )
    report.update({"outcome": "claim-unstated", "passed": False,
                   "implementation": {"name": "gw"},
                   "capture": {"target_must_be_preconfigured_for": "http://127.0.0.1:8765/v1"}})
    report["checks"].update({"sse_validity": {"passed": True},
                             "fragmentation_safety": {"passed": True},
                             "client_observed_latency": {"passed": True}})
    specimens = explain.OperatorSpecimens(
        fixture={"EMAIL": SPECIMEN_EMAIL}, prompt=f"contact {SPECIMEN_EMAIL}"
    )
    selfcheck._print_report(report, "LEAK", "raw values reached the upstream", None,
                            specimens=specimens)
    out = capsys.readouterr().out

    assert out.count(SPECIMEN_EMAIL) == 2, out
    assert "the provider saw:" in out
    assert "<-" in out


def test_the_ci_summary_explains_the_leak_without_publishing_a_specimen() -> None:
    """The summary lands in a pull request. Everyone who can read the PR reads it."""
    from pii_leak_benchmark import ci

    run = {
        "schema": "pii-leak-benchmark/operator-run/v1",
        "verdict": "LEAK",
        "reason": "Raw fixture values reached the upstream: EMAIL.",
        "contract": {"profile": "pii-v1", "duty": "restore", "seed": "gateway-ci-v1"},
        "entities": {"EMAIL": "leak", "SSN": "contained"},
        "required_checks": {"configured_upstream_boundary": False},
        "coverage": [],
        "findings": [
            explain.published_dict(
                explain.Finding(
                    kind=explain.KIND_LEAK_TO_PROVIDER,
                    entity_type="EMAIL",
                    carrier="chat-content",
                    sent=SPECIMEN_EMAIL,
                    received=SPECIMEN_EMAIL,
                    seed="gateway-ci-v1",
                )
            )
        ],
    }
    summary = ci.render_summary(run)

    assert SPECIMEN_EMAIL not in summary
    assert "<EMAIL>" in summary
    assert "the provider saw:" in summary


def test_a_published_finding_dict_carries_no_run_derived_string() -> None:
    """The dict is what lands in `current.json`, so it is checked as data, not as text."""
    payload = explain.published_dict(
        explain.Finding(
            kind=explain.KIND_FIDELITY,
            entity_type="EMAIL",
            carrier="chat-content",
            sent=SPECIMEN_EMAIL,
            received="[EMAIL_1]",
            seed="s",
        )
    )
    blob = json.dumps(payload)
    assert SPECIMEN_EMAIL not in blob and "[EMAIL_1]" not in blob
    assert payload["entity_type"] == "EMAIL"


# ------------------------------------------------------- the artifact, measured for real


def test_a_real_leaking_run_writes_no_specimen_into_its_published_json() -> None:
    """The end of the privacy rule, asserted against a real report rather than a mock.

    Every test above checks a renderer. This one runs the profile against the documented
    no-gateway floor -- where by construction every fixture value reaches the capture, so
    there is a specimen to leak on every entity at once -- and then searches the ENTIRE
    serialized report for each of them. It therefore also covers paths this change never
    touched: a specimen reaching the artifact through some other field, added later by
    someone who never read `values_published`.

    The operator's own copy is checked from the same run, so the two halves of the rule are
    proved against one measurement: absent from the artifact, present in the terminal.
    """
    from pii_leak_benchmark.http_profile import run_http_conformance

    specimens = explain.OperatorSpecimens()
    report = run_http_conformance(
        "capture://self",
        capture_port=0,
        iterations=1,
        include_credentials=True,
        fixture_seed="explanation-privacy-rule",
        specimens=specimens,
    )

    assert specimens.fixture, "the floor run produced no fixture to check against"
    published = json.dumps(report)
    for entity, value in specimens.fixture.items():
        assert value not in published, f"{entity} specimen reached the published report"

    findings = explain.findings_from_report(report, specimens)
    assert findings, "the no-gateway floor must produce findings"
    for finding in findings:
        if not finding.sent:
            continue
        assert finding.sent not in "\n".join(explain.render(finding, reveal=False))
        assert finding.sent not in json.dumps(explain.published_dict(finding))

    operator_view = "\n".join(
        line for f in findings for line in explain.render(f, reveal=True)
    )
    leaked = {f.entity_type for f in findings if f.kind == explain.KIND_LEAK_TO_PROVIDER}
    assert leaked, "the floor leaks every type; none were reported"
    for entity in leaked:
        assert specimens.fixture[entity] in operator_view


@pytest.mark.slow
def test_the_ci_command_leaves_no_specimen_in_any_file_it_writes(tmp_path: Any) -> None:
    """Every file `ci` writes is an artifact somebody else reads.

    `summary.md` is appended to GITHUB_STEP_SUMMARY and read by everyone who can see the
    pull request; `current.json` is uploaded and kept as a baseline. The target here is the
    documented no-gateway floor, so every fixture value is genuinely in flight and there is
    a specimen of every type available to leak into all four files at once.
    """
    from pii_leak_benchmark import ci
    from pii_leak_benchmark.operator_profile import seeded_fixture

    out = tmp_path / "pii-check"
    seed = "ci-artifact-privacy"
    fixture, _ = seeded_fixture(seed, True)
    # A concrete port, because `ci` refuses port 0: the gateway it normally manages has
    # to be told an address, and "whatever the kernel picks" is not one.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    exit_code = ci.main([
        "--target-base-url", "capture://self", "--out", str(out), "--seed", seed,
        "--iterations", "1", "--capture-port", str(port), "--duty", "anonymize",
    ])

    assert exit_code == 1, "the floor must report a leak, or this proves nothing"
    written = sorted(out.glob("*"))
    assert {path.name for path in written} >= {"current.json", "summary.md"}
    for path in written:
        text = path.read_text(encoding="utf-8")
        for entity, value in fixture.items():
            assert value not in text, f"{entity} specimen reached {path.name}"

    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "What leaked, and why it matters" in summary
    assert "the provider saw:" in summary
