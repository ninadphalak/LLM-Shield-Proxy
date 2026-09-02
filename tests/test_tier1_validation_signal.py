"""Structural validation on Tier 1 matches is a SIGNAL, never a redaction gate.

The corpus below documents the precision cost of the fail-safe boundary: the native
patterns match something in 17 of 22 ordinary business strings (77.3%, 18 spans).
Those results are deliberately retained because issuer tables and checksums cannot
prove a card-shaped value is non-PII.

Likewise, a bare 12--15 digit value can be an international telephone number written
without a leading plus. Punctuation is not an enforcement boundary, so native PHONE
matches are never dropped by structural classification either.

The false positives are deliberate and are NOT a bug to be optimised away:

* ``ddd-dd-dddd`` GL codes and cost centres are structurally identical to a real SSN.
  The SSA rules that could be applied (area 000/666, group 00, serial 0000) exclude
  none of them, so narrowing there would buy a cosmetic win with a real miss.
* ``4500123456789`` and ``4006381333931`` are 13 digits beginning with 4. Visa issues
  13- and 16-digit numbers. They are indistinguishable from a real card.
* ``9876543210987`` and ``5901234123457`` satisfy the Luhn checksum by coincidence.
* Other 13--16 digit values can be private-label, gift, or newly assigned cards even
  when this repository's finite IIN table does not know them; a typo can also make
  such a card fail Luhn.

The coverage floor is the point of the design: Luhn is never a gate, because a real
card with one transposed digit fails Luhn and must never reach an upstream.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.pii_engine import PIIEngine, classify_tier1_match

# ---------------------------------------------------------------------------
# The corpus. Every entry is ordinary business text containing no PII at all.
# ---------------------------------------------------------------------------
NON_PII: list[tuple[str, list[str]]] = [
    ("Order number 4500123456789 shipped on 2026-02-14.", ["4500123456789"]),
    ("Invoice INV-2026-0041 totalling 1234567890123 cents was posted.", ["1234567890123"]),
    ("Please quote reference 9876543210987 when you call the depot.", ["9876543210987"]),
    ("Purchase order 4500-1234-5678 was cancelled by the buyer.", ["4500-1234-5678"]),
    ("Our SAP document number is 0090001234567890 for that posting.", ["0090001234567890"]),
    ("Replace part 123-45-6789 with the superseding revision.", ["123-45-6789"]),
    ("SKU 5901234123457 is discontinued; use 4006381333931 instead.",
     ["5901234123457", "4006381333931"]),
    ("The bearing is catalogued as 6203-2RS-C3 in the parts index.", []),
    ("Assembly 300-12-4400 requires torque of 42 Nm.", ["300-12-4400"]),
    ("Tracking number 1Z999AA10123456784 was delivered Tuesday.", ["1Z999AA10123456784"]),
    ("Container MSCU 123-45-6789 cleared customs this morning.", ["123-45-6789"]),
    ("Consignment 7551234567890 is held at the bonded warehouse.", ["7551234567890"]),
    ("ISBN 978-0-13-235088-4 is the second edition.", ["978-0-13-235088-4"]),
    ("Standard 9781234567897 supersedes the 2019 revision.", ["9781234567897"]),
    ("The batch ran from 2026-01-01 to 2026-03-31 without incident.", []),
    ("We produced 1234567890123456 units across the whole programme.", ["1234567890123456"]),
    ("Meeting moved to 2026-02-14 at 09:30 in room 314-15-9265.", ["314-15-9265"]),
    ("Journal entry 200-10-3000 credits the suspense account.", ["200-10-3000"]),
    ("GL code 400-20-1100 maps to freight recovery.", ["400-20-1100"]),
    ("Cost centre 105-30-2200 absorbed the variance.", ["105-30-2200"]),
    ("The warehouse pallet rotation policy is under review this quarter.", []),
    ("Please confirm receipt of the crate handling addendum.", []),
]

# Values that MUST still be redacted. The first two are the whole argument for keeping
# Luhn out of the gate.
MUST_REDACT = [
    ("4111 1111 1111 1112", "one transposed digit; Luhn fails; still a card"),
    ("4532-1234-5678-9012", "this repository's own historical test card; Luhn fails"),
    ("4111-1111-1111-1111", "Luhn-valid Visa"),
    ("5555555555554444", "Luhn-valid Mastercard"),
    ("4242-4242-4242-4242", "Luhn-valid Visa"),
    ("6011-1111-1111-1117", "Luhn-valid Discover"),
    ("2223003122003222", "Mastercard 2-series"),
    ("378282246310005", "American Express, 15 digits"),
]


@pytest.fixture(scope="module")
def engine():
    return PIIEngine()


def _spans_over(engine, text, needle):
    index = text.find(needle)
    assert index >= 0
    return [
        (entity, matched)
        for start, end, entity, matched in engine.detect_spans(text)
        if start < index + len(needle) and index < end
    ]


def test_over_redaction_rate_on_ordinary_business_text(engine):
    """Record the honest precision cost of keeping every plausible card shape."""
    dirty = 0
    spans = 0
    for text, protected in NON_PII:
        hits = [hit for needle in protected for hit in _spans_over(engine, text, needle)]
        if hits:
            dirty += 1
            spans += len(hits)
    assert dirty == 17, f"documented corpus result changed: {dirty}/{len(NON_PII)} strings"
    assert spans == 18, f"documented corpus result changed: {spans} spans"


@pytest.mark.parametrize("value,why", MUST_REDACT)
def test_coverage_floor_every_card_shape_is_still_redacted(engine, value, why):
    """Coverage may never shrink. Luhn is not a gate: {why}."""
    text = f"Card on file: {value} for the account."
    hits = _spans_over(engine, text, value)
    assert any(entity == "CREDIT_CARD" for entity, _ in hits), (
        f"{value} ({why}) is no longer redacted as a card: {hits}"
    )


@pytest.mark.parametrize(
    "value",
    ["555-0199", "555-123-4567", "+1 555 123 4567", "(555) 123-4567", "5551234567"],
)
def test_real_phone_numbers_are_untouched_by_the_bare_run_rule(engine, value):
    text = f"Call the desk on {value} before noon."
    hits = _spans_over(engine, text, value)
    assert hits, f"{value} stopped being detected"


@pytest.mark.parametrize(
    "value,description",
    [
        ("442079460123", "UK country code and London number without a leading plus"),
        ("8613800138000", "China country code and mobile number without a leading plus"),
    ],
)
def test_bare_international_phone_number_is_never_dropped(engine, value, description):
    """Negative control: punctuation is not proof that a number is non-phone."""
    keep, confidence = classify_tier1_match("PHONE", value)
    assert keep is True, description
    assert confidence == "medium"

    text = f"Call the international desk on {value} before noon."
    hits = _spans_over(engine, text, value)
    assert any(entity == "PHONE" for entity, _ in hits), (
        f"{value} ({description}) escaped the real PHONE detection boundary: {hits}"
    )


@pytest.mark.parametrize("value", ["914-27-6083", "456-78-9012", "123-45-6789"])
def test_ssn_detection_is_deliberately_unchanged(engine, value):
    """A GL code and an SSN are the same shape. Fail closed and keep both."""
    text = f"Reference {value} in the filing."
    hits = _spans_over(engine, text, value)
    assert any(entity == "SSN" for entity, _ in hits), f"{value} stopped being redacted"


@pytest.mark.parametrize(
    "digits",
    ["0090001234567890", "1234567890123456", "9780132350884"],
)
def test_card_shaped_business_identifiers_are_kept_fail_safe(digits):
    """Namespace guesses are insufficient evidence to disclose a card-shaped value."""
    keep, confidence = classify_tier1_match("CREDIT_CARD", digits)
    assert keep is True
    assert confidence == "medium"


def test_a_failed_checksum_alone_never_drops_a_card():
    """The single rule that must not be weakened, asserted directly."""
    keep, confidence = classify_tier1_match("CREDIT_CARD", "4111111111111112")
    assert keep is True
    assert confidence == "medium", "a checksum failure must not read as high confidence"

    keep_valid, confidence_valid = classify_tier1_match("CREDIT_CARD", "4111111111111111")
    assert keep_valid is True
    assert confidence_valid == "high"


@pytest.mark.parametrize(
    "value,reason",
    [
        ("9876543210987658", "unrecognised IIN with a valid checksum"),
        ("9876543210987685", "same private-label shape with final digits transposed"),
        ("9876543210987657", "same private-label shape with a one-digit error"),
    ],
)
def test_unrecognised_private_label_shape_is_never_dropped(engine, value, reason):
    """Negative control: finite IIN knowledge and Luhn failure cannot cause a leak."""
    keep, confidence = classify_tier1_match("CREDIT_CARD", value)
    assert keep is True, reason
    assert confidence == "medium"

    text = f"Card on file: {value} for the account."
    hits = _spans_over(engine, text, value)
    assert any(entity == "CREDIT_CARD" for entity, _ in hits), (
        f"{value} ({reason}) escaped the real detection boundary: {hits}"
    )


def test_bare_digit_runs_are_not_rejected_as_phone_matches():
    keep, confidence = classify_tier1_match("PHONE", "4500123456789")
    assert keep is True
    assert confidence == "medium"
    assert classify_tier1_match("PHONE", "5551234567")[0] is True
    assert classify_tier1_match("PHONE", "555-0199")[0] is True


def test_validation_fails_closed_when_it_raises(engine, monkeypatch):
    """A validator that explodes must redact, not leak. Never the reverse."""
    import llm_shield_proxy.engines.pii_engine as module

    def boom(*_args, **_kwargs):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(module, "classify_tier1_match", boom)
    text = "Card on file: 4111-1111-1111-1111 for the account."
    hits = _spans_over(engine, text, "4111-1111-1111-1111")
    assert hits, "an exception in validation dropped a span instead of keeping it"


def test_unknown_entity_types_are_passed_through_unchanged():
    assert classify_tier1_match("EMAIL", "someone@example.com")[0] is True
    assert classify_tier1_match("IP_ADDRESS", "192.168.1.1")[0] is True
    assert classify_tier1_match("GITHUB_PAT", "ghp_x" * 8)[0] is True
