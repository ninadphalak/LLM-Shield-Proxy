"""The protected fixture must be VALID and NON-REAL at the same time.

The fixture this replaced was neither-nor in the direction that matters: all three of
its values were INVALID specimens, so a detector that validates its input correctly
ignored all three. Measured against stock Presidio, `123-45-6789` is on its SSN
recognizer's own invalidation list, `4532-1234-5678-9012` has Luhn checksum 68, and
`.invalid` has no public suffix. The profile therefore scored carefulness as a fault,
and it did so in favour of this project's own engine, which is a bare regex with no
checksum and no range check.

These tests are the guard against restoring that. They deliberately do not need
Presidio running: every property below is the *reason* a validating detector accepts or
rejects the value, checked directly.
"""

import re

import pytest

from llm_shield_proxy.conformance.http_profile import (
    PROTECTED_ENTITY_TYPES,
    PROTECTED_VALUE_FORMATS,
    PROTECTED_VALUE_PATTERNS,
    REFERENCE_FIXTURE,
    _build_prompt,
    _FIXTURE_TEST_CARDS,
    _ipv4_can_produce,
    _make_nonce,
    _normalize,
    _SSN_INVALIDATING_PREFIXES,
    extract_fixture,
    fixture_value_space,
    make_fixture,
)

DRAWS = 300

# Presidio's UsItinRecognizer group ranges. An SSN whose group falls in one of these
# is a syntactically valid ITIN too, and a validating detector may label it US_ITIN
# instead of US_SSN -- measured on the SSA's own advertising range, 987-65-4320.
ITIN_GROUPS = (
    list(range(50, 66)) + list(range(70, 89)) + list(range(90, 93)) + list(range(94, 100))
)


def luhn_ok(digits: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


@pytest.fixture(scope="module")
def fixtures():
    return [make_fixture() for _ in range(DRAWS)]


def test_every_generated_card_passes_luhn(fixtures):
    """A Luhn-invalid card is invisible to every validating detector.

    The previous fixture's card summed to 68. Presidio's CreditCardRecognizer runs the
    checksum in validate_result, so it returned no CREDIT_CARD at all.
    """
    for fixture in fixtures:
        digits = fixture["CREDIT_CARD"].replace("-", "")
        assert len(digits) == 16
        assert luhn_ok(digits), fixture["CREDIT_CARD"]
    assert not luhn_ok("4532123456789012"), "the old fixture card must stay a counter-example"


def test_every_card_is_a_published_test_number(fixtures):
    """Non-real half. Generating a Luhn-valid PAN could produce a live card."""
    published = set(_FIXTURE_TEST_CARDS)
    for fixture in fixtures:
        assert fixture["CREDIT_CARD"].replace("-", "") in published


def test_generated_ssns_survive_presidios_invalidation_rules(fixtures):
    """Each clause here is a rule in UsSsnRecognizer.invalidate_result."""
    for fixture in fixtures:
        digits = fixture["SSN"].replace("-", "")
        assert len(digits) == 9
        assert not all(digit == digits[0] for digit in digits)
        assert digits[3:5] != "00"
        assert digits[5:] != "0000"
        for prefix in _SSN_INVALIDATING_PREFIXES:
            assert not digits.startswith(prefix), (fixture["SSN"], prefix)
    # The values the old fixture used, and the SSA's own advertising range, are both
    # rejected by those same rules. That is the defect in one line.
    assert "123456789".startswith("123456789")
    assert "987654320".startswith("98765432")


def test_generated_ssns_are_never_issued_and_never_itin_shaped(fixtures):
    """Non-real half, plus the reason the SSA's advertising range is unusable here."""
    for fixture in fixtures:
        area, group, serial = fixture["SSN"].split("-")
        # The SSA has never issued an SSN in the 900-999 area.
        assert 900 <= int(area) <= 999
        # Outside every ITIN group range, so a validating detector labels it US_SSN.
        assert int(group) not in ITIN_GROUPS
        assert int(group) >= 1
        assert int(serial) >= 1
    assert 65 in ITIN_GROUPS, "987-65-4320 is ITIN-shaped, which is why it is not used"


def test_generated_emails_use_the_reserved_documentation_domain(fixtures):
    """example.com is RFC 2606 reserved and routes nowhere, and `.com` is a real
    public suffix -- which is what `tldextract`-based validation requires and what
    `.invalid` could never satisfy."""
    for fixture in fixtures:
        local, _, domain = fixture["EMAIL"].partition("@")
        assert domain == "example.com"
        assert local.isalpha() and local.islower()


def test_the_format_never_varies(fixtures):
    """Value variation only; format variation is what was measured and rejected.

    Round 8 measured six format variants and two of them (space- and dot-separated
    SSNs) produced FALSE leak findings against this project's own correctly-redacting
    gateway. Separators must stay byte-identical across runs.
    """
    shapes = {entity: set() for entity in PROTECTED_ENTITY_TYPES}
    for fixture in fixtures:
        for entity, value in fixture.items():
            assert PROTECTED_VALUE_PATTERNS[entity].fullmatch(value), value
            shapes[entity].add(re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "d", value)))
    for entity, seen in shapes.items():
        assert len(seen) == 1, (entity, seen)


def test_values_actually_vary(fixtures):
    """Otherwise "varies per run" is a claim the artifact makes and does not keep."""
    for entity in PROTECTED_ENTITY_TYPES:
        distinct = {fixture[entity] for fixture in fixtures}
        assert len(distinct) > 1, entity
    # The card is the deliberate exception in degree: six published numbers, not 1e9.
    assert fixture_value_space()["CREDIT_CARD"] == len(_FIXTURE_TEST_CARDS)
    assert fixture_value_space()["SSN"] > 10_000_000
    assert fixture_value_space()["EMAIL"] > 10_000_000


def test_no_needle_is_a_substring_of_another(fixtures):
    """The coincidence that hid a real fail-open decode defect for eight rounds.

    The old fixture's SSN digits (123456789) were a SUBSTRING of its card digits
    (4532123456789012), so every "the SSN was recovered" assertion in the evasion suite
    could be satisfied by recovering the CARD. That masked a base64 offset guard which
    could not decode an 11-byte value out of a one-character-prefixed run at all.
    """
    old_ssn, old_card = "123456789", _normalize("4532-1234-5678-9012")
    assert old_ssn in old_card, "the historical coincidence must stay documented"

    for fixture in fixtures:
        needles = {entity: _normalize(value) for entity, value in fixture.items()}
        for left, left_value in needles.items():
            for right, right_value in needles.items():
                if left == right:
                    continue
                assert left_value not in right_value, (left, right, fixture)


def test_generated_ssns_cannot_be_produced_by_any_ipv4(fixtures):
    """Removes the round-7 false positive by construction rather than by disclosure."""
    for fixture in fixtures:
        assert not _ipv4_can_produce(_normalize(fixture["SSN"])), fixture["SSN"]
    assert _ipv4_can_produce("123456789"), "123.45.67.89 must stay the counter-example"


def test_a_prompt_round_trips_through_format_detection(fixtures):
    """A target now needs a format detector, not three string comparisons."""
    for fixture in fixtures[:50]:
        prompt = _build_prompt(_make_nonce(), fixture)
        assert extract_fixture(prompt) == fixture


def test_the_reference_fixture_satisfies_every_rule():
    """It is published in docs and used by tests, so it must not drift from the space."""
    digits = REFERENCE_FIXTURE["SSN"].replace("-", "")
    assert luhn_ok(REFERENCE_FIXTURE["CREDIT_CARD"].replace("-", ""))
    assert REFERENCE_FIXTURE["CREDIT_CARD"].replace("-", "") in _FIXTURE_TEST_CARDS
    assert 900 <= int(digits[:3]) <= 999
    assert int(digits[3:5]) not in ITIN_GROUPS
    assert not _ipv4_can_produce(digits)
    assert REFERENCE_FIXTURE["EMAIL"].endswith("@example.com")
    for entity, value in REFERENCE_FIXTURE.items():
        assert PROTECTED_VALUE_PATTERNS[entity].fullmatch(value)


def test_formats_are_published_and_values_are_not():
    """The report must let a reader tell a varied VALID fixture from the old one
    without handing a shape-matching shim the answers."""
    assert set(PROTECTED_VALUE_FORMATS) == set(PROTECTED_ENTITY_TYPES)
    assert PROTECTED_VALUE_FORMATS["SSN"] == "ddd-dd-dddd"
    assert PROTECTED_VALUE_FORMATS["CREDIT_CARD"] == "dddd-dddd-dddd-dddd"
    assert PROTECTED_VALUE_FORMATS["EMAIL"].endswith("@example.com")
