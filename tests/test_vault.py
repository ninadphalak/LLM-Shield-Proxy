import pytest
from llm_shield_proxy.vault import Vault, VaultStore


def test_vault_deterministic_mapping():
    vault = Vault()
    t1 = vault.get_or_create_token("sarah@example.com", "EMAIL")
    assert t1 == "[EMAIL_1]"

    # Determinism: same value returns same token
    t2 = vault.get_or_create_token("sarah@example.com", "EMAIL")
    assert t2 == "[EMAIL_1]"

    # Second email gets next index
    t3 = vault.get_or_create_token("john@example.com", "EMAIL")
    assert t3 == "[EMAIL_2]"


def test_vault_rehydration():
    vault = Vault()
    t_person = vault.get_or_create_token("Sarah Connor", "PERSON")
    t_email = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    text = f"Contact {t_person} at {t_email} for assistance."
    rehydrated = vault.rehydrate(text)
    assert rehydrated == "Contact Sarah Connor at sarah@skynet.com for assistance."


def test_vault_store_session_persistence():
    store = VaultStore()
    v1 = store.get_vault("session-123")
    token_a = v1.get_or_create_token("555-0199", "PHONE")

    v2 = store.get_vault("session-123")
    token_b = v2.get_or_create_token("555-0199", "PHONE")

    assert token_a == token_b == "[PHONE_1]"

    # Ephemeral vault (no session ID) creates new vault each call
    e1 = store.get_vault(None)
    e2 = store.get_vault(None)
    assert e1 is not e2
