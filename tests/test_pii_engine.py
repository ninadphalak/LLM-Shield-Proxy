import pytest
from app.vault import Vault
from app.pii_engine import PIIEngine


def test_pii_tier1_structured_redaction():
    engine = PIIEngine(enable_tier2=False)
    vault = Vault()

    sample_text = (
        "User info: email john.doe@acme.org, SSN 123-45-6789, "
        "phone 555-123-4567, credit card 4532-1234-5678-9012, "
        "IP 192.168.1.1, API Key sk-1234567890abcdef1234567890abcdef"
    )

    redacted = engine.redact_text(sample_text, vault)

    assert "[EMAIL_1]" in redacted
    assert "[SSN_1]" in redacted
    assert "[PHONE_1]" in redacted
    assert "[CREDIT_CARD_1]" in redacted
    assert "[IP_ADDRESS_1]" in redacted
    assert "[API_KEY_1]" in redacted

    assert "john.doe@acme.org" not in redacted
    assert "123-45-6789" not in redacted

    rehydrated = vault.rehydrate(redacted)
    assert rehydrated == sample_text


def test_pii_tier2_ner_redaction():
    engine = PIIEngine(enable_tier2=True)
    vault = Vault()

    sample_text = "Please reach out to Dr. Sarah Connor for further details."
    redacted = engine.redact_text(sample_text, vault)

    assert "[PERSON_1]" in redacted
    assert "Dr. Sarah Connor" not in redacted

    rehydrated = vault.rehydrate(redacted)
    assert rehydrated == sample_text


def test_pii_payload_redaction():
    engine = PIIEngine(enable_tier2=True)
    vault = Vault()

    payload = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "My email is alice@example.com"}
        ],
        "temperature": 0.7
    }

    redacted_payload = engine.redact_payload(payload, vault)

    assert redacted_payload["messages"][0]["content"] == "My email is [EMAIL_1]"
    assert vault.token_to_original["[EMAIL_1]"] == "alice@example.com"
