"""Versioned operator workload, independent of the frozen publication fixtures."""

from __future__ import annotations

import hashlib
import random

PROFILE_VERSION = "operator-smoke/1"


def seeded_fixture(seed: str, credentials: bool) -> tuple[dict[str, str], str]:
    """Replay valid synthetic PII and fixed credential shapes, including prompt context."""
    from .http_profile import (
        _CREDENTIAL_VALUES,
        _FIXTURE_TEST_CARDS,
        _NONCE_WORD_COUNT,
        _NONCE_WORDS,
        _SSN_INVALIDATING_PREFIXES,
        _ipv4_can_produce,
    )

    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest(), "big"))  # nosec B311 - synthetic reproducibility
    local = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(8))
    while True:
        ssn = f"{rng.randint(900, 999):03d}-{rng.randint(1, 49):02d}-{rng.randint(1, 9999):04d}"
        digits = ssn.replace("-", "")
        if not _ipv4_can_produce(digits) and not any(digits.startswith(p) for p in _SSN_INVALIDATING_PREFIXES):
            break
    card = rng.choice(_FIXTURE_TEST_CARDS)
    fixture = {"EMAIL": local + "@example.com", "SSN": ssn,
               "CREDIT_CARD": "-".join(card[i:i + 4] for i in range(0, 16, 4))}
    if credentials:
        fixture.update(_CREDENTIAL_VALUES)
    remaining = list(_NONCE_WORDS)
    marker = "-".join(remaining.pop(rng.randrange(len(remaining))) for _ in range(_NONCE_WORD_COUNT))
    return fixture, marker


def coverage(formats: dict[str, str]) -> list[dict[str, str]]:
    from .http_profile import CREDENTIAL_ENTITY_TYPES, PROTECTED_ENTITY_TYPES

    known = set(CREDENTIAL_ENTITY_TYPES) | set(PROTECTED_ENTITY_TYPES)
    if set(formats) - known:
        raise ValueError("operator coverage metadata missing for: " + ", ".join(sorted(set(formats) - known)))
    return [{"entity": entity, "format": form,
             "variation": "fixed synthetic specimen" if entity in CREDENTIAL_ENTITY_TYPES
             else "seeded synthetic value"}
            for entity, form in sorted(formats.items())]
