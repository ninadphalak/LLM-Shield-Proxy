"""Axis A: all 72 entities must be present, and every generatable value must check out.

Two things are asserted here and they pull in opposite directions.

COMPLETENESS. The registry carries all 72 entities from the external derivation, and
the ones it cannot generate or cannot match are DECLARED EXCLUSIONS with a reason. A
registry that quietly shrank to its generatable rows would redefine the problem as "the
entities that happen to have regular expressions" -- which is the failure the external
derivation existed to prevent, one level up.

VALIDITY. Every value the registry will actually emit is recomputed here from the
algorithm, not trusted. This project has already shipped an invalid fixture once and
re-run everything, and it has already been caught by published folklore: 2222 2222 2222
is widely cited as a placeholder Aadhaar and is not Verhoeff-valid. An invalid value
silently advantages shape-matching detectors over validating ones, and the harm lands on
other people's products.
"""

import re

import pytest
from pii_leak_benchmark.entity_registry import (
    ALIAS,
    BY_ID,
    COUNT_DELTAS,
    DISPOSITIONS,
    ENTITIES,
    ENTITY_LIST_PUBLISHED_COUNTS,
    GENERATABLE,
    MAX_ENTITY_ID_LENGTH,
    NO_MATCHABLE_FORMAT,
    NOT_GENERATABLE,
    OUT_OF_TEXT_SCOPE,
    _normalize_for_collision,
    containment_collisions,
    corpus_entities,
    counts,
    declared_exclusions,
)
from pii_leak_benchmark.http_profile import _normalize

# Published by `.llm/research/entity-list.md` §7. Quoted, not recomputed.
PUBLISHED_REGIONS = {
    "global": 31,
    "us": 13,
    "uk": 6,
    "in": 5,
    "ca": 3,
    "au": 4,
    "sg": 3,
}
PUBLISHED_EU_TOTAL = 7  # eu plus the six member-state rows


# ---------------------------------------------------------------------------
# Completeness and shape
# ---------------------------------------------------------------------------


def test_all_seventy_two_entities_are_present():
    assert len(ENTITIES) == ENTITY_LIST_PUBLISHED_COUNTS["total"] == 72
    assert len(BY_ID) == 72, "duplicate entity id"


def test_every_entity_declares_a_disposition_and_a_reason():
    for entity in ENTITIES:
        assert entity["disposition"] in DISPOSITIONS, entity["id"]
        if entity["disposition"] != ALIAS:
            assert entity["reason"].strip(), f"{entity['id']} has no reason"


def test_the_exclusions_are_declared_rather_than_dropped():
    """The size of this set is itself a finding: it is what deriving an entity list
    from external authority costs, once you refuse blog-sourced format rules and refuse
    to invent an identifier that could be someone's."""
    assert len(corpus_entities()) + len(declared_exclusions()) == 72
    assert len(declared_exclusions()) > len(corpus_entities()), (
        "most of this list cannot be generated, and the registry must keep saying so"
    )


def test_entity_ids_fit_the_streaming_budget():
    """Ten ASCII characters. The vault's look-behind retention is L = N - 1 where N is
    the maximum placeholder length and the placeholder derives from the entity id, so
    the LONGEST id widens the SSE rehydration window for every request whether or not
    that entity is ever seen."""
    for entity in ENTITIES:
        assert len(entity["id"]) <= MAX_ENTITY_ID_LENGTH, entity["id"]
        assert entity["id"].isascii() and entity["id"].isupper(), entity["id"]
    assert "CREDIT_CARD" not in BY_ID and "CARDPAN" in BY_ID


def test_the_region_spread_matches_the_external_derivation():
    tally: dict[str, int] = {}
    for entity in ENTITIES:
        tally[entity["region"]] = tally.get(entity["region"], 0) + 1
    for region, expected in PUBLISHED_REGIONS.items():
        assert tally.get(region) == expected, f"{region}: {tally.get(region)} != {expected}"
    european = sum(
        count
        for region, count in tally.items()
        if region in ("eu", "de", "fr", "es", "it", "pl", "nl")
    )
    assert european == PUBLISHED_EU_TOTAL


def test_no_matchable_format_count_matches_the_published_figure():
    assert (
        counts()[NO_MATCHABLE_FORMAT]
        == ENTITY_LIST_PUBLISHED_COUNTS["no_matchable_format"]
        == 12
    )


def test_the_count_deltas_against_entity_list_are_recorded_not_hidden():
    """Two of §7's summary figures do not survive a cell-by-cell read of its own §3
    tables. Recording that is the point -- adjusting either side to match the other
    would destroy the only evidence that the criterion is ambiguous."""
    derived = counts()
    assert derived[NOT_GENERATABLE] != ENTITY_LIST_PUBLISHED_COUNTS["unsafe_to_generate"]
    assert derived["unverified"] != ENTITY_LIST_PUBLISHED_COUNTS["unverified"]
    assert COUNT_DELTAS.strip(), "the delta must be explained where a reader will find it"


def test_out_of_scope_entities_are_declared_rather_than_silently_passed():
    """A text-stream gateway cannot reach an image or an audio template. The honest
    result is not-applicable; a silent pass would credit a gateway for handling
    something it never saw."""
    out_of_scope = {entity["id"] for entity in ENTITIES if entity["disposition"] == OUT_OF_TEXT_SCOPE}
    assert out_of_scope == {"BIOTMPL", "FACEIMG", "VOICEPR"}


# ---------------------------------------------------------------------------
# Validity of everything the registry will actually emit
# ---------------------------------------------------------------------------


def test_every_generatable_entity_carries_a_value_and_nothing_else_does():
    for entity in ENTITIES:
        if entity["disposition"] == GENERATABLE:
            assert entity["synthetic"], f"{entity['id']} is generatable with no value"
        elif entity["id"] != "STREET":
            # STREET keeps a value for documentation but has no matchable format, so it
            # is excluded from the corpus rather than generated.
            assert not entity["synthetic"], (
                f"{entity['id']} is excluded but carries a value, which invites use"
            )


def _luhn_ok(digits, offset=0):
    total, parity = 0, len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (total + offset) % 10 == 0


def _mod97_ok(iban):
    rotated = iban[4:] + iban[:4]
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in rotated
    )
    return int(expanded) % 97 == 1


def _nhs_mod11_ok(digits):
    total = sum(int(digit) * weight for digit, weight in zip(digits[:9], range(10, 1, -1)))
    remainder = 11 - (total % 11)
    if remainder == 11:
        remainder = 0
    return remainder != 10 and remainder == int(digits[9])


def _aba_ok(digits):
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    return sum(int(d) * w for d, w in zip(digits, weights)) % 10 == 0


def _vin_ok(vin):
    values = {**{str(d): d for d in range(10)}}
    for index, letter in enumerate("ABCDEFGHJKLMNPRSTUVWXYZ"):
        values[letter] = [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9][index]
    weights = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(values[character] * weight for character, weight in zip(vin, weights))
    expected = total % 11
    return vin[8] == ("X" if expected == 10 else str(expected))


CHECKED = {
    "IMEI": lambda value: _luhn_ok(value),
    "CARDPAN": lambda value: _luhn_ok(value),
    "IBAN": _mod97_ok,
    "NHSNUM": _nhs_mod11_ok,
    "ABART": _aba_ok,
    "VIN": _vin_ok,
}


@pytest.mark.parametrize("entity_id", sorted(CHECKED))
def test_each_checksummed_value_is_recomputed_here(entity_id):
    """Never ship a value whose validity you have not computed yourself.

    The Aadhaar folklore is the standing example: `2222 2222 2222` is cited everywhere
    as a placeholder and fails Verhoeff. Had it shipped as a positive fixture, every
    gateway that validates would have been scored as missing an identifier it was
    correct to ignore -- the invalid-fixture bug, aimed at other people's products.
    """
    entity = BY_ID[entity_id]
    assert entity["disposition"] == GENERATABLE
    value = re.sub(r"[^0-9A-Za-z]", "", entity["synthetic"])
    assert CHECKED[entity_id](value), f"{entity_id} value {value!r} fails its own checksum"


def test_the_registrys_collision_fold_matches_the_matchers():
    """The registry reimplements the fold so it stays importable without the harness.
    If the two ever disagree, the collision set is computed against a rule the matcher
    does not use, and the exclusions it produces protect nothing."""
    for entity in corpus_entities():
        assert _normalize_for_collision(entity["synthetic"]) == _normalize(entity["synthetic"]), (
            entity["id"]
        )


def test_containment_collisions_are_computed_and_not_wished_away():
    """`_ipv4_can_produce` exists because an SSN's digits are reconstructible from an
    IPv4 address. That is a CLASS, not a special case: the matcher strips separators,
    so wherever one entity's normalized value is a substring of another's, a case
    carrying both reports the shorter leaked whenever the longer does.

    These pairs cannot be designed away. The only never-assigned ZIP is `00000`; the
    only routing number satisfying the 3-7-1 weighting without belonging to a district
    is `000000000`; a sort code has no checksum, so `00-00-00` is the most synthetic
    value available. "Obviously synthetic" and "all zeros" are nearly the same
    requirement, so these entities collide by construction. The corpus builder treats
    them as mutually exclusive within a case instead.
    """
    pairs = containment_collisions()
    assert pairs, "the check must be live, not vacuously satisfied"
    assert ("ABART", "ZIP5") in pairs and ("UKSORT", "ZIP5") in pairs
    # Every collision is inside the zero cluster. A pair outside it is a value chosen
    # carelessly rather than a structural constraint, and should be fixed at the value.
    zero_cluster = {"ZIP5", "UKSORT", "ABART", "GEOCOORD", "GENETIC", "NHSNUM"}
    outside = [pair for pair in pairs if not set(pair) <= zero_cluster]
    assert not outside, f"avoidable collision, pick a different value: {outside}"


def test_the_avoidable_collision_stays_fixed():
    """CARDEXP was `12/34`, which normalizes to `1234` and is a substring of the IBAN,
    the SSN and the NINO values. Nothing about it was structural -- any other month and
    year works -- and the containment check is what found it."""
    assert BY_ID["CARDEXP"]["synthetic"] == "12/99"
    expiry = _normalize_for_collision("12/99")
    for other in ("IBAN", "SSN", "NINO"):
        assert expiry not in _normalize_for_collision(BY_ID[other]["synthetic"])


# ---------------------------------------------------------------------------
# Aadhaar, which is where the folklore was
# ---------------------------------------------------------------------------


def _verhoeff_ok(digits):
    d = (
        (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
        (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
        (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
        (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
        (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
        (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
        (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
        (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
        (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
    )
    p = (
        (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
        (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
        (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
        (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
        (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
        (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
        (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
    )
    check = 0
    for index, digit in enumerate(reversed(digits)):
        check = d[check][p[index % 8][int(digit)]]
    return check == 0


def test_the_verhoeff_implementation_agrees_with_canonical_vectors():
    """Validate the checker before believing anything it says about Aadhaar."""
    assert _verhoeff_ok("2363")
    assert _verhoeff_ok("123451")
    assert _verhoeff_ok("758722")
    assert not _verhoeff_ok("2364")


def test_the_widely_cited_placeholder_aadhaar_is_not_valid():
    assert not _verhoeff_ok("222222222222"), "the folklore value must stay rejected"


def test_the_repeated_digit_values_are_checksum_valid_and_scheme_invalid():
    """The correction that goes one level deeper than the folklore.

    333333333333, 666666666666 and 999999999999 DO pass Verhoeff and the first-digit
    2-9 rule -- and the scheme also excludes palindromes, so all three are
    scheme-invalid. An earlier revision used them as VALID Aadhaar fixtures, which was
    the same class of error as the 2222 folklore one layer down.
    """
    for value in ("333333333333", "666666666666", "999999999999"):
        assert _verhoeff_ok(value)
        assert value[0] in "23456789"
        assert value == value[::-1], "these are palindromes, and the scheme excludes them"


def test_aadhaar_is_not_generatable_and_its_value_is_not_publishable():
    """The canonical checksum- AND scheme-valid value 234567890124 is not obviously
    synthetic, and Aadhaar has no reserved test range -- so it could be someone's.
    Aadhaar Act s. 29(4) forbids publishing an Aadhaar number. The registry therefore
    declines to generate rather than shipping a value with a caveat attached.
    """
    aadhaar = BY_ID["AADHAAR"]
    assert aadhaar["disposition"] == NOT_GENERATABLE
    assert aadhaar["synthetic"] is None
    assert aadhaar["publishable"] is False
    assert _verhoeff_ok("234567890124") and "234567890124" != "234567890124"[::-1]


def test_the_aadhaar_negative_control_is_a_negative_control():
    """Shape drives redaction and validity is a confidence signal, so a scheme-invalid
    value must STILL be redacted. That is what the repeated-digit string is retained
    for, and it must never be reused as a positive fixture."""
    control = BY_ID["AADHAAR"]["negative_control"]
    assert control == "333333333333"
    assert _verhoeff_ok(control), "the control is checksum-valid on purpose"
    assert control == control[::-1], "and scheme-invalid on purpose"
