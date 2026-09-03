"""The PERSON corpus, repurposed as proof that nothing is fabricated.

This file began as a before/after measurement of a regex ``PERSON`` heuristic. That
heuristic has been deleted (see the comment block above ``TIER3_NER_ENTITIES`` in
``llm_shield_proxy/engines/pii_engine.py``), and the corpus now does a different job: it
is the standing evidence that with no NER model loaded the engine invents **nothing**.

**Why the heuristic went.** Measured over these same 60 strings, it fired on 25 of 25
ordinary business sentences containing a capitalized bigram and produced 26 fabricated
names, while detecting 0 of 5 CJK, Hangul, Cyrillic or Arabic names. Under synthetic
swapping "My Aadhaar is on the enrolment slip." became "Elizabeth is on the enrolment
slip." -- grammatical English no downstream consumer could tell had been altered. A Tier 1
false positive over-redacts, which is safe; a PERSON false positive replaces real text,
which is not. Tightening it was measured too: a grammatical determiner rule cut the false
positives to 4 of 25 but silently lost 4 of 4 genuine names introduced by a determiner
("the Jane Doe account"). Both directions were bad, so the detector was removed rather
than tuned, and the gap is now stated loudly instead.

**The contract these tests assert.**

1. With no ONNX NER model loaded, ``detect_spans`` produces **no PERSON span at all** --
   over every string here, including the 20 that genuinely contain a name.
2. Ordinary prose survives ``redact_text`` byte-for-byte, in synthetic-swap mode, which is
   the mode where the old corruption was invisible.
3. The absence of name redaction is reported: by ``describe_ner_coverage()``, by a startup
   warning naming the profile, by ``/health`` and ``/readyz``, and in the compliance pack.

**Why the name-bearing strings stay.** They are not dead weight. The 20 in
``NAMED_PEOPLE`` are exactly the cases a model-backed run must catch, so this corpus stays
useful the moment ``SHIELD_TEST_ONNX_MODEL_DIR`` is pointed at a real model -- see
``tests/test_tier3_onnx_ner.py``. Recording them here also keeps the cost of the removal
visible: those 15 Latin-script names WERE detected by the old heuristic and are not
detected now. That is a real reduction in coverage, accepted because the same detector
corrupted 25 of 25 non-name strings to get them.

**Corpus provenance.** These 60 strings were hand-written for this test. They are not
sampled from natural traffic and no rate derived from them belongs in the benchmark report
or in marketing. Names are common given/family names combined arbitrarily; they identify
no one.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

# ---------------------------------------------------------------------------
# Slice 1: ordinary prose containing a capitalized bigram that is NOT a person.
# Every false positive here is content corruption -- the token replaces real text.
# ---------------------------------------------------------------------------
CAPITALIZED_NON_NAMES = [
    "My Aadhaar is on the enrolment slip.",
    "The Finance Team reviewed the quarterly close last week.",
    "Our Data Protection Officer signed off on the transfer.",
    "Please open a Support Ticket before escalating further.",
    "The Change Advisory Board meets every Thursday morning.",
    "Send the Purchase Order to the Accounts Payable inbox.",
    "This Service Level Agreement covers business hours only.",
    "The Incident Response Plan was updated in March.",
    "Our Chief Information Security Officer approved the exception.",
    "The Board Meeting minutes are stored in the shared drive.",
    "Machine Learning models are retrained every quarter.",
    "The Human Resources portal is down for maintenance.",
    "Read the Terms And Conditions before signing.",
    "The Federal Reserve raised rates again this month.",
    "European Union rules apply to this data transfer.",
    "Update the Standard Operating Procedure accordingly.",
    "The Quality Assurance stage caught the regression.",
    "Our Disaster Recovery site is in another region.",
    "The Annual Report will be published in April.",
    "New York offices close early on Friday.",
    "Please review the Risk Register before the audit.",
    "The Steering Committee deferred the decision.",
    "Business Continuity testing happens twice a year.",
    "The Security Operations Centre triaged the alert.",
    "My Passport expired last summer.",
]

# ---------------------------------------------------------------------------
# Slice 2: the control. Ordinary prose with no capitalized bigram at all.
# ---------------------------------------------------------------------------
PLAIN_PROSE = [
    "The warehouse pallet rotation policy is under review this quarter.",
    "Please confirm receipt of the crate handling addendum.",
    "We shipped the remaining units on the second lorry.",
    "The invoice totals were reconciled without incident.",
    "Retention windows are configured per tenant in the policy file.",
    "There is no outstanding balance on this account.",
    "Latency rose slightly after the last deployment.",
    "Send the amended figures before the close of business.",
    "All of the affected records were restored from backup.",
    "The certificate renews automatically every ninety days.",
    "Nothing in the log suggests an authentication failure.",
    "Costs were absorbed within the existing budget line.",
    "We recommend enabling the durable audit sink in production.",
    "The container was rebuilt against the newer base image.",
    "Throughput held steady across the whole test window.",
]

# ---------------------------------------------------------------------------
# Slice 3: genuine person names. Each entry is (text, the name that must be caught).
# The final five are non-Latin scripts: the deleted regex could not express them at
# all, and they are the starting point for the international name work.
# ---------------------------------------------------------------------------
NAMED_PEOPLE = [
    ("Please contact Jane Doe about the invoice.", "Jane Doe"),
    ("The claim was filed by Robert Smith yesterday.", "Robert Smith"),
    ("Dr. Alice Brown reviewed the imaging results.", "Alice Brown"),
    ("Mr. Thomas Clark signed the consent form.", "Thomas Clark"),
    ("Escalate to Maria Garcia in the billing team.", "Maria Garcia"),
    ("The account belongs to William Turner.", "William Turner"),
    ("Ask Sarah Mitchell for the ledger export.", "Sarah Mitchell"),
    ("The referral came from Daniel Hughes.", "Daniel Hughes"),
    ("Prof. Emily Watson chaired the review panel.", "Emily Watson"),
    ("Notify Michael Anderson of the outcome.", "Michael Anderson"),
    ("The patient Linda Parker was discharged.", "Linda Parker"),
    ("Route the request to Kevin Murphy.", "Kevin Murphy"),
    ("The signatory is Patricia Nolan.", "Patricia Nolan"),
    ("Follow up with James Whitfield tomorrow.", "James Whitfield"),
    ("The report was authored by Susan Reid.", "Susan Reid"),
    ("Contact 田中太郎 regarding the shipment.", "田中太郎"),
    ("Please email 김민준 about the transfer.", "김민준"),
    ("The applicant is Иван Петров from the Riga office.",
     "Иван Петров"),
    ("Forward the file to محمد الأحمد for review.",
     "محمد الأحمد"),
    ("The reviewer 李伟 approved the change.", "李伟"),
]

LATIN_SCRIPT_NAMES = NAMED_PEOPLE[:15]
NON_LATIN_SCRIPT_NAMES = NAMED_PEOPLE[15:]

# ---------------------------------------------------------------------------
# Held-out slices. Written AFTER the determiner rule was chosen, to probe it in both
# directions rather than to confirm it. Slice 1 above was inspected while designing the
# rule, so its post-fix number is optimistic by construction; these four are not.
# ---------------------------------------------------------------------------

# The rule's cost. A genuine name introduced by a determiner is now a MISS. This is an
# under-redaction and it is the price of the precision gain -- record it, do not hide it.
NAMES_INTRODUCED_BY_A_DETERMINER = [
    ("The Jane Doe account was closed in March.", "Jane Doe"),
    ("Escalate to the Robert Smith case file.", "Robert Smith"),
    ("Our Maria Garcia deputised for the shift lead.", "Maria Garcia"),
    ("Please review this Susan Reid submission.", "Susan Reid"),
]

# Names with no determiner in sight. The rule must not touch these.
SENTENCE_INITIAL_NAMES = [
    ("Jane Doe called about the invoice.", "Jane Doe"),
    ("Robert Smith filed the claim yesterday.", "Robert Smith"),
    ("Michael Anderson approved the exception.", "Michael Anderson"),
]

# An honorific is direct evidence of a person and overrides the determiner rule, so a
# titled name survives even in a position where a bare name would not.
TITLED_NAMES = [
    ("Dr. Alice Brown reviewed the scan.", "Alice Brown"),
    ("The Dr. Alice Brown referral is attached.", "Alice Brown"),
    ("Mr. Thomas Clark signed.", "Thomas Clark"),
]

# The residual false positives the rule does NOT fix: a capitalized organisation name at
# the start of a sentence has no determiner to give it away. Separating noun from verb in
# what follows needs a POS tagger, not a regex, so these stay.
SENTENCE_INITIAL_ORGANISATIONS = [
    "Machine Learning is hard to operationalise.",
    "European Union guidance was published today.",
    "Business Continuity remains the top risk.",
    "Global Payments processed the batch overnight.",
]


@pytest.fixture(scope="module")
def engine() -> PIIEngine:
    """The default engine: Tier 3 enabled, no ONNX model (conftest turns the model off).

    This is the shipped default for almost every deployment, which is why it is the
    configuration the no-fabrication contract is asserted against.
    """
    return PIIEngine()


def _person_spans(engine: PIIEngine, text: str) -> list[str]:
    return [
        matched for _start, _end, entity, matched in engine.detect_spans(text) if entity == "PERSON"
    ]


ALL_STRINGS = (
    CAPITALIZED_NON_NAMES
    + PLAIN_PROSE
    + [text for text, _name in NAMED_PEOPLE]
    + [text for text, _name in NAMES_INTRODUCED_BY_A_DETERMINER]
    + [text for text, _name in SENTENCE_INITIAL_NAMES]
    + [text for text, _name in TITLED_NAMES]
    + SENTENCE_INITIAL_ORGANISATIONS
)


# ---------------------------------------------------------------------------
# 1. Nothing is fabricated.
# ---------------------------------------------------------------------------


def test_no_model_means_no_person_span_anywhere_in_the_corpus(engine):
    """The whole contract in one assertion, over all 74 strings.

    Not "few" false positives and not "tuned" ones: none. There is no heuristic left that
    could produce a PERSON span, so any hit here means a fallback was reintroduced.
    """
    assert engine.name_redaction_active is False, (
        "this test asserts the no-model contract; a model is loaded"
    )
    offenders = [(text, hits) for text in ALL_STRINGS if (hits := _person_spans(engine, text))]
    assert offenders == [], f"a PERSON span was produced without a model: {offenders}"


@pytest.mark.parametrize("text", CAPITALIZED_NON_NAMES)
def test_ordinary_prose_survives_redaction_byte_for_byte(engine, text):
    """Synthetic-swap mode, the mode where the old corruption was undetectable.

    Every one of these 25 strings was rewritten by the previous heuristic.
    """
    assert engine.redact_text(text, Vault(synthetic=True)) == text


@pytest.mark.parametrize("text", PLAIN_PROSE)
def test_plain_prose_survives_redaction_byte_for_byte(engine, text):
    assert engine.redact_text(text, Vault(synthetic=True)) == text


def test_the_specific_reproduction_that_motivated_the_removal(engine):
    """"My Aadhaar is on the enrolment slip." became "Elizabeth is on the enrolment slip."."""
    text = "My Aadhaar is on the enrolment slip."
    assert engine.redact_text(text, Vault(synthetic=True)) == text
    assert engine.redact_text(text, Vault(synthetic=False)) == text


# ---------------------------------------------------------------------------
# 2. The cost of the removal, recorded rather than described.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,name", LATIN_SCRIPT_NAMES, ids=[name for _text, name in LATIN_SCRIPT_NAMES]
)
def test_recorded_cost_latin_script_names_are_no_longer_detected(engine, text, name):
    """These 15 WERE detected by the deleted heuristic. They are not detected now.

    This is a genuine reduction in redaction coverage and it is asserted, not footnoted,
    so that it cannot quietly stop being true in either direction. A model-backed run must
    catch every one of these: see tests/test_tier3_onnx_ner.py.
    """
    assert name not in " ".join(_person_spans(engine, text))


@pytest.mark.parametrize(
    "text,name", NON_LATIN_SCRIPT_NAMES, ids=[str(i) for i in range(len(NON_LATIN_SCRIPT_NAMES))]
)
def test_recorded_non_latin_names_were_never_detected_either(engine, text, name):
    """0/5 before the removal and 0/5 after: the old regex was ASCII-only by construction.

    Recorded so the removal is not blamed for a gap it did not create, and so the
    international name work has a measured starting point.
    """
    assert _person_spans(engine, text) == []


# ---------------------------------------------------------------------------
# 3. The gap is loud.
# ---------------------------------------------------------------------------


def test_coverage_report_states_that_name_redaction_is_inactive(engine):
    coverage = engine.describe_ner_coverage()

    assert coverage["name_redaction_active"] is False
    assert coverage["model_loaded"] is False
    assert "global_strict" in coverage["profiles_expecting_ner"]
    assert coverage["unbacked_profiles"] == coverage["profiles_expecting_ner"]
    assert "no heuristic fallback" in coverage["reason"]


def test_a_profile_declaring_person_without_a_model_logs_a_warning(caplog):
    """The startup signal, emitted once per profile (re)compile.

    Rebuilding the profiles is what triggers it, which means a policy hot-reload that
    newly declares PERSON is reported on a long-running process too.
    """
    engine = PIIEngine()
    caplog.clear()
    with caplog.at_level("WARNING", logger="llm_shield_proxy.engines.pii_engine"):
        engine._init_custom_regex()

    from llm_shield_proxy.engines.pii_engine import NER_DISABLED_WARNING

    messages = [record.getMessage() for record in caplog.records]
    assert NER_DISABLED_WARNING in messages, messages

    # The message is deliberately prose for an operator and names no profile. The
    # per-profile detail is structured data, asserted on describe_ner_coverage() above
    # and on /readyz in tests/test_health_and_alerts.py.
    assert "global_strict" in engine.describe_ner_coverage()["unbacked_profiles"]


def test_the_warning_is_silent_when_no_profile_expects_ner(caplog):
    """No false alarm for a deployment that never asked for name redaction."""
    engine = PIIEngine()
    engine._global_strict_profile.tier3_ner_entities = set()
    engine._compiled_profiles.clear()

    # The constructor above legitimately warns; this test is about the next call only.
    caplog.clear()
    with caplog.at_level("WARNING", logger="llm_shield_proxy.engines.pii_engine"):
        engine._warn_if_ner_is_declared_but_unbacked()

    assert not [
        record for record in caplog.records if "Name redaction" in record.getMessage()
    ]
