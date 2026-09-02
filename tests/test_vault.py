from llm_shield_proxy.engines.vault import Vault, VaultStore


def test_vault_deterministic_mapping():
    vault = Vault(synthetic=False)
    t1 = vault.get_or_create_token("sarah@example.com", "EMAIL")
    assert t1 == "[EMAIL_1]"

    # Determinism: same value returns same token
    t2 = vault.get_or_create_token("sarah@example.com", "EMAIL")
    assert t2 == "[EMAIL_1]"

    # Second email gets next index
    t3 = vault.get_or_create_token("john@example.com", "EMAIL")
    assert t3 == "[EMAIL_2]"


def test_vault_rehydration():
    vault = Vault(synthetic=False)
    t_person = vault.get_or_create_token("Sarah Connor", "PERSON")
    t_email = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    text = f"Contact {t_person} at {t_email} for assistance."
    rehydrated = vault.rehydrate(text)
    assert rehydrated == "Contact Sarah Connor at sarah@skynet.com for assistance."


def test_vault_synthetic_swapping_deterministic():
    vault = Vault(synthetic=True)
    t_person = vault.get_or_create_token("Sarah Connor", "PERSON")
    assert "[" not in t_person and "]" not in t_person
    # Determinism: same value returns same synthetic token
    t_person2 = vault.get_or_create_token("Sarah Connor", "PERSON")
    assert t_person == t_person2

    text = f"Patient {t_person} attended appointment."
    assert vault.rehydrate(text) == "Patient Sarah Connor attended appointment."


def test_vault_store_session_persistence():
    store = VaultStore()
    v1 = store.get_vault("session-123")
    v1.synthetic = False
    token_a = v1.get_or_create_token("555-0199", "PHONE")

    v2 = store.get_vault("session-123")
    token_b = v2.get_or_create_token("555-0199", "PHONE")

    assert token_a == token_b == "[PHONE_1]"

    # Ephemeral vault (no session ID) creates new vault each call
    e1 = store.get_vault(None)
    e2 = store.get_vault(None)
    assert e1 is not e2


def test_configured_vault_encryption_key_is_used(monkeypatch):
    """A configured VAULT_ENCRYPTION_KEY must produce a stable DEK, not a per-process one."""
    import hashlib

    from llm_shield_proxy.core.config import settings
    from llm_shield_proxy.engines import vault as vault_module

    monkeypatch.setattr(settings, "VAULT_ENCRYPTION_KEY", "operator-supplied-key")
    monkeypatch.setattr(vault_module, "_PROCESS_DEK", None)

    expected = hashlib.sha256(b"operator-supplied-key").digest()
    assert vault_module.get_vault_dek() == expected
    assert vault_module.get_vault_dek() == expected


def test_unset_vault_encryption_key_falls_back_to_ephemeral_dek(monkeypatch):
    from llm_shield_proxy.core.config import settings
    from llm_shield_proxy.engines import vault as vault_module

    monkeypatch.setattr(settings, "VAULT_ENCRYPTION_KEY", None)
    monkeypatch.setattr(vault_module, "_PROCESS_DEK", None)

    dek = vault_module.get_vault_dek()
    assert len(dek) == 32
    assert vault_module.get_vault_dek() == dek
