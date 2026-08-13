import pytest
from llm_shield_proxy.vault import Vault
from llm_shield_proxy.pii_engine import PIIEngine


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
    assert "[AWS_API_KEY_1]" in redacted

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


def test_dlp_redos_base64_obfuscation():
    """
    Simulate a Red Team attack attempting to cause ReDoS (Regex Denial of Service)
    by passing massive Base64-encoded strings (obfuscated AWS/GitHub keys or JSON payloads)
    to ensure the engine doesn't hang.
    """
    import base64
    import time
    engine = PIIEngine(enable_tier2=False)
    vault = Vault()
    
    # Create a massive obfuscated payload simulating an attempt to hide secrets
    base_secret = "AKIAIOSFODNN7EXAMPLE" * 1000  # Repeat a mock AWS key
    encoded_secret = base64.b64encode(base_secret.encode("utf-8")).decode("utf-8")
    
    malicious_prompt = f"Please analyze this dataset: {encoded_secret}" * 10
    
    start_time = time.time()
    redacted = engine.redact_text(malicious_prompt, vault)
    end_time = time.time()
    
    # It should not hang (e.g. process under 50ms)
    assert (end_time - start_time) < 0.1, f"ReDoS detected! Took {end_time - start_time} seconds"
    
    # Our regex is currently NOT designed to decode base64, so it shouldn't crash, 
    # but the test validates that the runtime remains tightly bounded (no ReDoS).
    assert encoded_secret in redacted
