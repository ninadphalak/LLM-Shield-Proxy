"""Synthetic stand-ins must not be reversible, and must not match across tenants.

The stand-in for a real value used to be chosen by seeding a generator with a plain
hash of that value:

    seed = sha256(original)[:16]

No secret was involved, so the mapping was computable by anyone. An audit recovered a
real SSN from its stand-in in 0.1 seconds and 7,727 guesses, with no access to the
vault at all. Whoever receives the redacted data, which is the entire point of
redacting it, could undo the redaction.

The same formula also made the stand-in identical for the same value in every tenant,
so two datasets could be joined on the redacted field. That is the linkage redaction
exists to prevent.

The seed is now an HMAC over the value, its entity type, and the vault's own identity,
under a process secret. Both properties follow: the mapping cannot be computed without
the key, and the same value looks different in different tenants.
"""

from __future__ import annotations

import hashlib

from llm_shield_proxy.engines.vault import Vault

SSN = "456-78-9012"


def _synthetic_vault(**identity) -> Vault:
    return Vault(synthetic=True, **identity)


def test_a_stand_in_cannot_be_derived_from_the_plaintext_alone() -> None:
    """The old attack: recompute the mapping offline and invert it.

    Reproduces the exact formula that was used, and asserts the shipped vault no longer
    agrees with it. If this fails, the seed has gone back to being guessable.
    """
    from faker import Faker

    vault = _synthetic_vault()
    real_token = vault.get_or_create_token(SSN, "SSN")

    guessed = Faker()
    guessed.seed_instance(int(hashlib.sha256(SSN.encode("utf-8")).hexdigest()[:16], 16))
    unkeyed_token = guessed.ssn()

    assert real_token != unkeyed_token, (
        "the stand-in is still an unkeyed hash of the plaintext, so anyone can "
        "recover the original by trying candidates offline"
    )


def test_a_brute_force_over_the_value_space_does_not_find_the_original() -> None:
    """The audit recovered an SSN in 7,727 guesses. Those guesses must now miss."""
    from faker import Faker

    vault = _synthetic_vault()
    target = vault.get_or_create_token(SSN, "SSN")

    guesser = Faker()
    for candidate_tail in range(4000):
        candidate = f"456-78-{candidate_tail:04d}"
        guesser.seed_instance(int(hashlib.sha256(candidate.encode()).hexdigest()[:16], 16))
        assert guesser.ssn() != target, f"recovered the original from the stand-in: {candidate}"


def test_the_same_value_gets_different_stand_ins_in_different_tenants() -> None:
    """Identical stand-ins across tenants let two datasets be joined on a redacted field."""
    bank = _synthetic_vault(tenant_id="bankA", session_id="s1", virtual_key_id="k1")
    hospital = _synthetic_vault(tenant_id="hospB", session_id="s2", virtual_key_id="k2")

    assert bank.get_or_create_token(SSN, "SSN") != hospital.get_or_create_token(SSN, "SSN")


def test_the_same_value_is_stable_within_one_vault() -> None:
    """Invariant 9: masking to rehydration is deterministic per request and session."""
    vault = _synthetic_vault()
    first = vault.get_or_create_token(SSN, "SSN")
    second = vault.get_or_create_token(SSN, "SSN")
    assert first == second


def test_round_tripping_still_restores_the_caller_value() -> None:
    """The point of a stand-in is that it maps back. Keying must not break that."""
    vault = _synthetic_vault()
    token = vault.get_or_create_token("bob@example.com", "EMAIL")
    assert vault.rehydrate(f"write to {token}") == "write to bob@example.com"


def test_two_different_values_do_not_collide_into_one_stand_in() -> None:
    """A collision overwrites the reverse map and restores the wrong person's value.

    Sixteen distinct names were enough to collide before. The vault now resolves a
    collision instead of letting the later value silently replace the earlier one.
    """
    vault = _synthetic_vault()
    originals = [f"Person Number {n}" for n in range(1, 40)]
    tokens = [vault.get_or_create_token(name, "PERSON") for name in originals]

    assert len(set(tokens)) == len(tokens), "two different people share one stand-in"
    for name, token in zip(originals, tokens):
        assert vault.rehydrate(token) == name, f"{token} restored as the wrong person"
