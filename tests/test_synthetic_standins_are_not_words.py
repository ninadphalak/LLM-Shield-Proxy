"""A synthetic stand-in must never be text a model writes anyway.

For entity types without a dedicated Faker generator (encoded values such as a
percent-encoded URL, digit runs, medical record numbers, national insurance numbers, any
type the vault does not name) the stand-in used to be `fake.word()`, a common English word.
Restoring a reply then replaced EVERY standalone use of that word with the caller's value:
a reply that said "where" or "call" in ordinary prose got the caller's secret pasted in
there. The value only went back to the same caller, but the reply was wrong and the secret
appeared where the model wrote an ordinary word.
"""

from __future__ import annotations

import re

import pytest
from faker.providers.lorem.en_US import Provider as LoremProvider

from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault

FALLBACK_TYPES = [
    "PERCENT_OBFUSCATED_PII",
    "BASE64_OBFUSCATED_PII",
    "ENTITY_OBFUSCATED_PII",
    "LONG_DIGIT_RUN",
    "MRN",
    "UK_NINO",
    "SOME_FUTURE_TYPE",
]
SIGNED_URL = (
    "https://backups.example.com/exports/customers.csv?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20261112%2Fus-east-1%2Fs3%2Faws4_request"
    "&X-Amz-Signature=" + "0123456789abcdef" * 4
)
WORDS = sorted(set(LoremProvider.word_list))
PROSE = (
    "Where is it? I can call you, his team will use the news, and we seem to have an hour "
    "of business left. " + " ".join(WORDS)
)


def _vault(session: str) -> Vault:
    return Vault(session_id=session, synthetic=True)


@pytest.mark.parametrize("entity_type", FALLBACK_TYPES)
def test_fallback_standins_are_never_dictionary_words(entity_type):
    for i in range(200):
        token = _vault(f"s{i}").get_or_create_token(f"value-{i}-{entity_type}", entity_type)
        assert token not in WORDS, f"{entity_type} got the dictionary word {token!r}"
        assert not token.isalpha(), f"{entity_type} got a plain word {token!r}"
        assert re.search(r"[\d_]", token), token


@pytest.mark.parametrize("entity_type", FALLBACK_TYPES)
def test_restore_leaves_ordinary_prose_alone_and_still_restores_the_standin(entity_type):
    for i in range(50):
        vault = _vault(f"r{i}")
        secret = f"secret-{i}-{entity_type}"
        token = vault.get_or_create_token(secret, entity_type)
        assert vault.rehydrate(PROSE, retention_length=0) == PROSE
        reply = f"{PROSE} Your value is {token}."
        restored = vault.rehydrate(reply, retention_length=0)
        assert restored == f"{PROSE} Your value is {secret}."
        assert restored.count(secret) == 1


def test_the_reported_case_a_signed_url_is_restored_only_where_its_standin_is():
    spans = pii_engine.detect_spans(f"link: {SIGNED_URL} ok")
    assert spans, "precondition: the signed URL must be detected"
    vault = _vault("signed-url")
    for _start, _end, entity_type, matched in spans:
        token = vault.get_or_create_token(matched, entity_type)
        reply = f"Here is the link: {token}. Where else can I look? I can call it again."
        restored = vault.rehydrate(reply, retention_length=0)
        assert restored.count(matched) == 1
        assert restored.endswith("Where else can I look? I can call it again.")


def test_standins_are_stable_within_a_vault_and_differ_across_vaults():
    a, b = _vault("same"), _vault("same")
    assert a.get_or_create_token("x-1", "MRN") == b.get_or_create_token("x-1", "MRN")
    assert _vault("one").get_or_create_token("x-1", "MRN") != _vault("two").get_or_create_token("x-1", "MRN")


def test_formats_follow_the_type():
    vault = _vault("formats")
    assert re.fullmatch(r"QQ\d{6}[A-D]", vault.get_or_create_token("AB123456C", "UK_NINO"))
    assert re.fullmatch(r"9\d{11}", vault.get_or_create_token("123456789012345", "LONG_DIGIT_RUN"))
    assert re.fullmatch(r"9\d{11}", vault.get_or_create_token("MRN 00012345", "MRN"))
    assert re.fullmatch(r"ref_[a-z2-9]{12}", vault.get_or_create_token("a%40b.example", "PERCENT_OBFUSCATED_PII"))


def test_a_vault_saved_before_the_change_still_restores_its_word_standin():
    """Persisted sessions keep their stored mapping; nothing is re-minted on load."""
    old = Vault(synthetic=True)
    old.load_state(
        {
            "original_to_token": {SIGNED_URL: "where"},
            "token_to_original": {"where": SIGNED_URL},
            "one_way_original_to_token": {},
            "type_counters": {"PERCENT_OBFUSCATED_PII": 1},
            "max_token_length": 5,
        }
    )
    assert old.rehydrate("link: where", retention_length=0) == f"link: {SIGNED_URL}"
    assert old.get_or_create_token(SIGNED_URL, "PERCENT_OBFUSCATED_PII") == "where"
