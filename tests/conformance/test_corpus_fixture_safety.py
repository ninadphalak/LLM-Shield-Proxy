"""Every corpus entity must be safe to publish, and the corpus must say when that costs
detectability.

A fixture value has to be BOTH safe to publish and detectable by the detectors under test.
Those two requirements conflict for identifiers validated against real assignment, and the
conflict is resolved only when the governing body publishes a reserved range for fictitious
use AND detectors honour it.

Measured against Google Cloud DLP on 2026-09-04:

    EMAIL    example.com (RFC 2606)                     detected
    CARDPAN  published brand test PANs                  detected
    USPHONE  NXX-555-0100..0199 (NANP fictitious)       detected
    SSN      SSA advertising block 987-65-4320..4329    NOT detected

SSN is the exception because its reserved block sits inside area 900-999, which is exactly
what a validating detector rejects: its reserved range and its detectable range are
disjoint. The corpus resolves that in favour of safety and reports the cost through
`metrics.detector_blind_entities`.

These tests pin the safety property. They cannot pin detectability, which depends on the
target and is therefore measured per run rather than asserted here.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

import pii_leak_benchmark.entity_registry as registry  # noqa: E402
from pii_leak_benchmark.v2_emitter import AXES, make_seeded_fixture  # noqa: E402

SEEDS = [str(n) for n in range(60)]


def _fixtures():
    return [make_seeded_fixture(random.Random(seed)) for seed in SEEDS]


def test_every_corpus_entity_is_in_the_registry() -> None:
    """The corpus must not invent an entity the registry has not dispositioned."""
    known = {entity["id"] for entity in registry.ENTITIES}
    assert set(AXES["entity"]) <= known, f"not in registry: {set(AXES['entity']) - known}"


def test_every_corpus_entity_declares_a_reserved_range() -> None:
    """Each one is safe for a stated reason, recorded in the registry rather than assumed."""
    by_id = {entity["id"]: entity for entity in registry.ENTITIES}
    for entity_id in AXES["entity"]:
        assert by_id[entity_id]["reserved_range"], (
            f"{entity_id} has no reserved_range recorded, so nothing states why publishing "
            "generated values of it is safe"
        )


def test_ssn_values_stay_in_the_never_assigned_range() -> None:
    """Area 900-999 is never assigned, which is the whole reason SSN is safe here.

    If this ever drifts into the assignable range the corpus starts emitting values that
    could belong to a living person, which is a far worse failure than a detector miss.
    """
    for fixture in _fixtures():
        area, group, serial = fixture["SSN"].split("-")
        assert 900 <= int(area) <= 999, f"SSN {fixture['SSN']} is outside the 900-series"
        assert group != "00" and serial != "0000"


def test_phone_values_stay_in_the_fictitious_block() -> None:
    """NXX-555-0100..0199. Anything outside it may be a real subscriber's number."""
    pattern = re.compile(r"^[2-9]\d{2}-555-01\d{2}$")
    for fixture in _fixtures():
        assert pattern.match(fixture["USPHONE"]), (
            f"{fixture['USPHONE']} is outside the 555-0100..0199 fictitious block"
        )


def test_card_values_are_published_test_pans() -> None:
    """Drawn from the brand-published list, never generated to satisfy Luhn alone: a
    Luhn-valid PAN that is not a published test number may be a live account."""
    from pii_leak_benchmark.http_profile import _FIXTURE_TEST_CARDS

    published = {card for card in _FIXTURE_TEST_CARDS}
    for fixture in _fixtures():
        assert fixture["CARDPAN"].replace("-", "") in published


def test_email_values_use_a_reserved_documentation_domain() -> None:
    for fixture in _fixtures():
        assert fixture["EMAIL"].endswith("@example.com")


def test_entities_are_distinguishable_from_each_other() -> None:
    """An SSN is 3-2-4 digits and a US phone is 3-3-4. If a fixture of one ever matched
    the shape of another, a leak of one would be scored as a leak of the other."""
    ssn_shape = re.compile(r"^\d{3}-\d{2}-\d{4}$")
    phone_shape = re.compile(r"^\d{3}-\d{3}-\d{4}$")
    for fixture in _fixtures():
        assert ssn_shape.match(fixture["SSN"])
        assert not phone_shape.match(fixture["SSN"])
        assert phone_shape.match(fixture["USPHONE"])
        assert not ssn_shape.match(fixture["USPHONE"])
