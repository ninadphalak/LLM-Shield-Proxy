"""Regression tests for four measured defects in the shipped Tier 1 / Tier 3 detectors.

These are **not** missing features. Each test below reproduces behaviour that is live in
``llm_shield_proxy/engines/pii_engine.py`` today, measured through the public
``PIIEngine.detect_spans()`` / ``PIIEngine.redact_text()`` entry points. They are expected
to FAIL until the corresponding fix lands; they are the acceptance criteria for step 1 of
``.llm/research/international-pii-new-window-prompt.md``.

The four defects, in the order they appear below:

1. **Partial redaction leaks digits.** ``PHONE`` matches a proper *prefix* of a longer
   grouped digit run. ``3333 3333 3333`` yields one span over ``3333 3333`` only, so the
   trailing ``3333`` reaches the upstream provider verbatim. A partial match is worse than
   a miss: the output looks redacted.
2. **``CREDIT_CARD`` is bounded at 16 digits.** ISO/IEC 7812-1 permits PANs of 17-19
   digits. A Luhn-valid, Visa-IIN 19-digit PAN matches nothing at all (the ASCII boundary
   assertions stop a 16-digit prefix from matching), so it passes through untouched.
3. **``PHONE`` mistypes non-phone identifiers.** Thirteen distinct national and financial
   identifiers are redacted, but tokenised and audited as ``PHONE``. The value does not
   leak; the audit record and the vault entity type are wrong.
4. **Tier 3 ``PERSON`` rewrites ordinary text.** Any two capitalized words not in a short
   exclusion list become a fabricated name, so ``My Aadhaar`` is replaced with a synthetic
   first name. This corrupts non-PII content silently.

**Fixture safety.** Every checksum below was computed locally, not cited (see
``.llm/research/entity_list_checks.py``), and then cross-checked against a second
independent implementation (``python-stdnum`` 2.2).

``2222 2222 2222`` is widely repeated as a placeholder Aadhaar and is **not**
Verhoeff-valid; it is not used here.

The repeated-digit strings ``333333333333`` / ``666666666666`` / ``999999999999`` pass
Verhoeff and satisfy the first-digit 2-9 rule, and an earlier revision of this file used
them as *valid* Aadhaar fixtures. That was wrong in the same way the ``2222`` folklore is
wrong, one level deeper: the Aadhaar scheme also excludes palindromes, so a repeated-digit
string is checksum-valid but **scheme-invalid**. ``stdnum.in_.aadhaar.is_valid`` rejects
all three. They are retained below only as the negative control -- a value that must be
redacted on shape alone even though it would fail validation -- and are labelled as such.

``234567890124`` is the canonical valid fixture: Verhoeff-valid (verified twice, by this
project's own implementation and by ``stdnum``), first digit 2, not a palindrome.

Two caveats travel with it and must not be dropped. First, the palindrome and 12-digit
Verhoeff rules come from UIDAI technical documentation that could not be retrieved
directly; the Aadhaar Act 2016 and the Enrolment Regulations, both retrieved in full, say
only that the number "shall be a random number" (s. 4(2)). Second, and unlike the
repeated-digit strings, ``234567890124`` is **not obviously synthetic**: no reserved test
range exists for Aadhaar, so a scheme-valid value could in principle belong to a real
person, and Aadhaar Act **s. 29(4)** forbids publishing an Aadhaar number. It is used here
because a detector test needs a scheme-valid shape, and it is kept out of any published
artifact.

The long PANs are Visa-prefixed bodies with a locally computed Luhn check digit. They are
not issued numbers, but no payment network publishes a reserved test range, so they carry
the same caveat as any card fixture in this suite.
"""

from __future__ import annotations

import re

import pytest

from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

# ---------------------------------------------------------------------------
# Fixtures. Validity computed locally; see the module docstring.
# ---------------------------------------------------------------------------

# Scheme-valid: Verhoeff-valid, first digit 2-9, not a palindrome. Verified by this
# project's implementation and independently by stdnum.in_.aadhaar.
AADHAAR_VALID = "234567890124"

# Checksum-valid but scheme-invalid (palindromes). Kept deliberately: the project's
# invariant is that shape drives redaction and validity is only a confidence signal, so
# these MUST still be redacted. Do not describe them as valid Aadhaar numbers.
AADHAAR_CHECKSUM_ONLY = ["333333333333", "666666666666", "999999999999"]

AADHAAR_VALUES = [AADHAAR_VALID] + AADHAAR_CHECKSUM_ONLY

# The conventional printed grouping. This is the grouping that leaked.
AADHAAR_GROUPED = [
    "2345 6789 0124",
    "3333 3333 3333",
    "6666 6666 6666",
    "9999 9999 9999",
]

# Luhn-valid, Visa IIN, at each length ISO/IEC 7812-1 permits above 16.
LONG_PANS = [
    ("41111111111111113", 17),
    ("411111111111111118", 18),
    ("4111111111111111110", 19),
]

# Identifiers that are currently redacted under the wrong entity type. Taken from
# `.llm/research/entity-list.md` section 8, reproducible via `entity_list_tally.py`.
# Not re-derived here: that file is the authority on formats and reserved ranges.
PHONE_MISTYPED = [
    ("SSN_bare", "900123456", "US SSN written without separators (900-999 is reserved invalid)"),
    ("EIN", "12-3456789", "US Employer Identification Number"),
    ("NPI", "1234567893", "US National Provider Identifier"),
    ("MRN_alt", "MRN 4471902", "medical record number in its labelled form"),
    ("ABART", "000000000", "US ABA routing number"),
    ("USACCT", "0012345678", "US bank account number"),
    ("NHSNUM", "9990000018", "UK NHS number, 999 reserved test range"),
    ("UKUTR", "1234567890", "UK Unique Taxpayer Reference"),
    ("DESTID", "12345678901", "German tax identification number"),
    ("PLPESEL", "44051401359", "Polish PESEL"),
    ("NLBSN", "123456782", "Dutch Burgerservicenummer"),
    ("AADHAAR", "234567890124", "Indian Aadhaar, unspaced and scheme-valid"),
    ("AUABN", "51824753556", "Australian Business Number"),
]


@pytest.fixture(scope="module")
def engine() -> PIIEngine:
    return PIIEngine()


def _vault() -> Vault:
    """Placeholder mode, not synthetic swapping: the assertions are about what survives."""
    return Vault(synthetic=False)


def _spans_over(engine: PIIEngine, text: str, needle: str):
    index = text.find(needle)
    assert index >= 0
    return [
        (entity, matched)
        for start, end, entity, matched in engine.detect_spans(text)
        if start < index + len(needle) and index < end
    ]


# ---------------------------------------------------------------------------
# Defect 1 -- partial redaction leaks the tail of a grouped identifier.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grouped", AADHAAR_GROUPED)
def test_grouped_twelve_digit_identifier_does_not_leak_its_tail(engine, grouped):
    """No digit of a grouped 12-digit identifier may survive redaction.

    Measured today: ``Aadhaar 3333 3333 3333`` redacts to ``Aadhaar [PHONE_1] 3333``.
    ``PHONE`` consumes ``3333 3333`` and stops, so four digits reach the upstream.

    This test deliberately does NOT assert the entity type. Which detector wins the span
    is step 5's business; step 1's obligation is only that nothing leaks. A fix that types
    the whole value ``PHONE`` satisfies this test and is still an improvement, because a
    mistyped-but-complete redaction is a wrong audit record rather than a disclosure.
    """
    text = f"Aadhaar {grouped} on file."
    redacted = engine.redact_text(text, _vault())

    digits = grouped.replace(" ", "")
    leaked = [
        digits[position : position + 4]
        for position in range(0, len(digits), 4)
        if digits[position : position + 4] in redacted
    ]
    assert not leaked, (
        f"digit groups {leaked} of {grouped!r} survived redaction: {redacted!r}"
    )


@pytest.mark.parametrize("grouped", AADHAAR_GROUPED)
def test_a_tier1_span_never_stops_mid_identifier(engine, grouped):
    """The general invariant behind defect 1, stated independently of Aadhaar.

    A numeric span must not end immediately before more of the same digit run. Ending a
    match at ``3333 3333`` when ``3333`` follows is a partial match, and a partial match on
    a structured identifier is a leak by construction.
    """
    spans = engine.detect_spans(grouped)
    assert spans, f"{grouped!r} is not detected at all"
    for start, end, entity, matched in spans:
        tail = grouped[end:]
        assert not tail[:2].strip().isdigit(), (
            f"{entity} span {matched!r} stops mid-run; {tail!r} follows"
        )


@pytest.mark.parametrize("value", AADHAAR_VALUES)
def test_unspaced_twelve_digit_identifier_is_fully_consumed(engine, value):
    """Control for the test above: the unspaced form already redacts completely.

    This passes today. It is here so that a fix to the grouped form cannot quietly
    regress the form that currently works.
    """
    redacted = engine.redact_text(f"Aadhaar {value} on file.", _vault())
    assert value not in redacted
    assert not re.search(r"\d{4}", redacted), f"digits survived: {redacted!r}"


# ---------------------------------------------------------------------------
# Defect 2 -- 17-19 digit PANs are outside the CREDIT_CARD length bound.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pan,length", LONG_PANS)
def test_iso_7812_long_pan_is_redacted_as_a_card(engine, pan, length):
    """ISO/IEC 7812-1 permits 17-19 digit PANs; ``{13,16}`` misses all of them.

    ``4111111111111111110`` is Luhn-valid, carries the Visa IIN, and is untouched today.
    That is a straight PCI DSS leak, not a typing problem.
    """
    text = f"Card on file: {pan} for the account."
    hits = _spans_over(engine, text, pan)
    assert any(entity == "CREDIT_CARD" for entity, _ in hits), (
        f"{length}-digit Luhn-valid PAN {pan} is not detected as a card: {hits}"
    )


@pytest.mark.parametrize("pan,length", LONG_PANS)
def test_long_pan_does_not_reach_the_upstream(engine, pan, length):
    """The disclosure statement of the test above, independent of entity type."""
    redacted = engine.redact_text(f"Card on file: {pan}.", _vault())
    assert pan not in redacted, f"{length}-digit PAN passed through: {redacted!r}"


# ---------------------------------------------------------------------------
# Defect 3 -- PHONE swallows non-phone identifiers and mistypes them.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Defect 3 is open. Closing it needs the entity types from step 5 of "
        ".llm/research/international-pii-new-window-prompt.md -- there is no correct type "
        "to assign these values to yet, and narrowing PHONE instead would turn a mistype "
        "into a miss. strict=True so this flips loudly the moment the patterns land."
    ),
)
@pytest.mark.parametrize("name,value,description", PHONE_MISTYPED, ids=[row[0] for row in PHONE_MISTYPED])
def test_non_phone_identifier_is_not_audited_as_a_phone_number(engine, name, value, description):
    """The value is redacted; the entity type recorded against it is wrong.

    A wrong type corrupts the audit record and the vault placeholder, and it makes the
    per-entity-type detection metric in the benchmark uninterpretable -- leak rate and
    correct-typing rate are different numbers and must be reported separately.

    This test cannot go green in step 1 alone: for most of these values the correct type
    does not exist in ``TIER1_PATTERNS`` yet. It is the step-5 acceptance criterion,
    recorded now so the fix to ``PHONE`` is measured against the right end state rather
    than against "no longer detected".
    """
    hits = _spans_over(engine, value, value.split()[-1] if " " in value else value)
    assert hits, f"{name} ({description}) is no longer detected at all -- coverage regression"
    assert not any(entity == "PHONE" for entity, _ in hits), (
        f"{name} ({description}) is typed as PHONE: {hits}"
    )


# ---------------------------------------------------------------------------
# Defect 4 -- Tier 3 PERSON fabricates names out of ordinary capitalized text.
# ---------------------------------------------------------------------------

ORDINARY_CAPITALIZED_TEXT = [
    "My Aadhaar is on the enrolment slip.",
    "Our Data Protection Officer signed off on the transfer.",
    "The Change Advisory Board meets every Thursday morning.",
    "This Service Level Agreement covers business hours only.",
    "Read the Terms And Conditions before signing.",
]


@pytest.mark.parametrize("text", ORDINARY_CAPITALIZED_TEXT)
def test_person_does_not_rewrite_ordinary_capitalized_text(engine, text):
    """``My Aadhaar is 3333 3333 3333`` currently becomes ``Elizabeth is ...``.

    Replacing non-PII prose with a fabricated name is worse than a miss: the corruption is
    invisible downstream because the output is well-formed English.

    See ``tests/test_person_precision_corpus.py`` for the before/after measurement that
    must accompany any fix here. Tightening this pattern trades false positives against
    genuine name detection, and the trade has to be measured, not asserted.
    """
    person_spans = [
        matched for _start, _end, entity, matched in engine.detect_spans(text) if entity == "PERSON"
    ]
    assert not person_spans, f"fabricated PERSON {person_spans} in ordinary text: {text!r}"


def test_person_rewrite_is_visible_end_to_end(engine):
    """The full measured reproduction, including the synthetic-swap output."""
    text = "My Aadhaar is 3333 3333 3333"
    redacted = engine.redact_text(text, _vault())
    assert "[PERSON" not in redacted, (
        f"'My Aadhaar' was replaced by a PERSON token: {redacted!r}"
    )
