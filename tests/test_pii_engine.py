"""Unit tests for 3-Tier PII & Secret Detection Engine."""

import base64
import json
import os
import tempfile
import time

import yaml

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import PIIEngine, calculate_shannon_entropy
from llm_shield_proxy.engines.vault import Vault


def test_shannon_entropy_calculation():
    """Validates mathematical computation of Shannon entropy."""
    # Low entropy (repetitive characters)
    low_entropy = calculate_shannon_entropy("aaaaaaaaaaaaaaaa")
    assert low_entropy == 0.0

    # High entropy (random alphanumeric secret key)
    high_entropy_key = "aB3$9zK!7wQ#2mP*5xL@"
    entropy_val = calculate_shannon_entropy(high_entropy_key)
    assert entropy_val >= 4.0


def test_pii_custom_regex_byor():
    """Tests Tier 1.5 BYOR custom regex injection from YAML config."""
    custom_yaml = {
        "custom_patterns": [
            {
                "name": "INTERNAL_EMPLOYEE_ID",
                "pattern": r"(?i)EMP-[A-Z]{3}-\d{5}",
                "description": "Matches internal Acme Corp employee IDs",
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as tmp:
        yaml.dump(custom_yaml, tmp)
        tmp_path = tmp.name

    try:
        # Override settings for the duration of the test
        original_path = settings.CUSTOM_REGEX_PATH
        settings.CUSTOM_REGEX_PATH = tmp_path

        # Initialize engine (should load custom regex from tmp_path)
        engine = PIIEngine(enable_tier2=False, enable_tier3=False)
        vault = Vault(synthetic=False)

        sample_text = "The new sysadmin is EMP-ABC-12345."
        redacted = engine.redact_text(sample_text, vault)

        # Assuming google-re2 is installed or fallback re works.
        assert "[INTERNAL_EMPLOYEE_ID_1]" in redacted
        assert "EMP-ABC-12345" not in redacted
    finally:
        settings.CUSTOM_REGEX_PATH = original_path
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_pii_tier1_structured_redaction():
    """Tests Tier 1 DFA regex extraction for structured patterns."""
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)

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


def test_pii_tier1_github_pat_redaction():
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    sample_text = f"GitHub token: {secret}"

    redacted = engine.redact_text(sample_text, vault)

    assert "[GITHUB_PAT_1]" in redacted
    assert secret not in redacted
    assert vault.rehydrate(redacted) == sample_text


def test_pii_tier1_ssh_private_key_redaction():
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    secret = "-----BEGIN OPENSSH PRIVATE KEY-----"
    sample_text = f"Leaked key header: {secret}"

    redacted = engine.redact_text(sample_text, vault)

    assert "[SSH_PRIVATE_KEY_1]" in redacted
    assert secret not in redacted
    assert vault.rehydrate(redacted) == sample_text


def test_pii_tier1_jwt_token_redaction():
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    secret = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    sample_text = f"JWT: {secret}"

    redacted = engine.redact_text(sample_text, vault)

    assert "[JWT_TOKEN_1]" in redacted
    assert secret not in redacted
    assert vault.rehydrate(redacted) == sample_text


def test_pii_tier1_mrn_redaction():
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    secret = "123-45-67A"
    sample_text = f"Medical record number: {secret}"

    redacted = engine.redact_text(sample_text, vault)

    assert "[MRN_1]" in redacted
    assert secret not in redacted
    assert vault.rehydrate(redacted) == sample_text


def test_pii_tier2_shannon_entropy_redaction():
    """Tests Tier 2 Shannon entropy detection for raw unformatted high-entropy secrets."""
    engine = PIIEngine(enable_tier2=True, enable_tier3=False, entropy_threshold=4.5)
    vault = Vault(synthetic=False)

    # Highly random 32-character secret key with high entropy (>= 4.5 bits)
    raw_high_entropy_secret = "9fK7w2mP8xL1qA4zD6eR0tY3uI5oN8vB"
    sample_text = f"Here is the database secret key: {raw_high_entropy_secret}"

    redacted = engine.redact_text(sample_text, vault)
    assert "[SECRET_KEY_1]" in redacted
    assert raw_high_entropy_secret not in redacted

    rehydrated = vault.rehydrate(redacted)
    assert rehydrated == sample_text


def test_tier3_without_a_model_redacts_no_names_and_says_so():
    """Tier 3 is model-backed only; there is no regex heuristic behind it.

    This test previously asserted that "Dr. Sarah Connor" was redacted by the regex
    fallback. That fallback was deleted on 2026-09-02: measured over a 60-string prose
    corpus it fabricated a name in 25 of 25 ordinary business sentences, and under
    synthetic swapping the corrupted output was grammatical English no consumer could
    detect. The name genuinely is not redacted now, so that is what is asserted, together
    with the fact that the engine reports the gap instead of hiding it.

    See tests/test_person_precision_corpus.py for the full contract and its cost, and
    tests/test_tier3_onnx_ner.py for the model-backed path that does detect this name.
    """
    engine = PIIEngine(enable_tier2=False, enable_tier3=True)
    vault = Vault(synthetic=False)

    sample_text = "Please reach out to Dr. Sarah Connor for further details."
    redacted = engine.redact_text(sample_text, vault)

    assert redacted == sample_text, "no NER model is loaded, so nothing may be invented"
    assert engine.name_redaction_active is False

    coverage = engine.describe_ner_coverage()
    assert coverage["name_redaction_active"] is False
    assert "no heuristic fallback" in coverage["reason"]


def test_pii_payload_redaction():
    """Tests deep recursive payload redaction across OpenAI message schemas."""
    engine = PIIEngine(enable_tier2=True, enable_tier3=True)
    vault = Vault(synthetic=False)

    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "My email is alice@example.com"}],
        "temperature": 0.7,
    }

    redacted_payload = engine.redact_payload(payload, vault)

    assert redacted_payload["messages"][0]["content"] == "My email is [EMAIL_1]"
    assert vault.token_to_original["[EMAIL_1]"] == "alice@example.com"


def test_pii_synthetic_swapping():
    """Tests realistic unbracketed synthetic entity swapping (Method B)."""
    engine = PIIEngine(enable_tier2=True, enable_tier3=True)
    vault = Vault(synthetic=True)

    sample_text = "Patient John Doe visited our Boston clinic. Contact john.doe@example.com"
    redacted = engine.redact_text(sample_text, vault)

    # Asserts no bracket placeholders are present
    assert "[" not in redacted and "]" not in redacted
    assert "john.doe@example.com" not in redacted

    # "John Doe" survives: Tier 3 is model-backed only and no model is loaded here. The
    # deleted regex heuristic would have swapped it for a fabricated first name, which is
    # exactly the silent corruption this build refuses to produce. Structured identifiers
    # are unaffected, which is what this test is actually about.
    assert "John Doe" in redacted
    assert engine.name_redaction_active is False

    rehydrated = vault.rehydrate(redacted)
    assert rehydrated == sample_text


def test_dlp_redos_base64_obfuscation():
    """Verifies that oversized Base64 blobs are skipped gracefully within bounded execution time without ReDoS."""
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)

    base_secret = "AKIAIOSFODNN7EXAMPLE" * 1000
    encoded_secret = base64.b64encode(base_secret.encode("utf-8")).decode("utf-8")
    malicious_prompt = f"Please analyze this dataset: {encoded_secret}" * 10

    start_time = time.perf_counter()
    redacted = engine.redact_text(malicious_prompt, vault)
    duration = time.perf_counter() - start_time

    assert duration < 0.1, f"ReDoS detected! Execution took {duration} seconds"
    # Oversized base64 blobs are deliberately skipped to prevent regex denial of service
    assert encoded_secret in redacted


def test_oversized_base64_keeps_plaintext_boundary_detection():
    """Skipping an encoded interior must not hide nearby plaintext PII."""
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    encoded_body = base64.b64encode(("attachment-data" * 1000).encode("utf-8")).decode("utf-8")

    redacted = engine.redact_text(
        f"Contact jane.doe@example.com | {encoded_body} | SSN 555-44-3333",
        vault,
    )

    assert encoded_body in redacted
    assert "jane.doe@example.com" not in redacted
    assert "555-44-3333" not in redacted


def test_tool_calls_and_embeddings_redaction():
    """Tests redaction inside agentic tool_calls and vector embeddings input fields."""
    engine = PIIEngine(enable_tier2=True, enable_tier3=True)
    vault = Vault(synthetic=False)

    # 1. Tool calls
    tool_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "lookup_patient",
                            "arguments": '{"ssn": "555-44-3333", "email": "alice@example.com"}',
                        },
                    }
                ],
            },
            {"role": "user", "name": "John_Smith", "content": "Review patient data"},
        ],
    }

    redacted_tool = engine.redact_payload(tool_payload, vault)
    args_str = redacted_tool["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert "555-44-3333" not in args_str
    assert "alice@example.com" not in args_str
    assert "[SSN_1]" in args_str
    assert "[EMAIL_1]" in args_str
    assert "John_Smith" not in redacted_tool["messages"][1]["name"]

    # 2. Embeddings payload
    embed_payload = {
        "input": ["Patient John Smith SSN 555-44-3333", "Record for Bob 123-45-6789"],
        "model": "text-embedding-3-small",
    }
    redacted_embed = engine.redact_payload(embed_payload, vault)
    assert "555-44-3333" not in redacted_embed["input"][0]
    assert "123-45-6789" not in redacted_embed["input"][1]


def test_subword_boundary_safe_rehydration():
    """Ensures synthetic words like 'May' do not corrupt legitimate words like 'Maybe'."""
    vault = Vault(synthetic=True)
    vault.token_to_original["May"] = "Sarah"
    vault.max_token_length = 3

    text = "Maybe we should check with May tomorrow."
    rehydrated = vault.rehydrate(text)

    # 'Maybe' should remain 'Maybe', while standalone 'May' should be rehydrated to 'Sarah'
    assert "Maybe" in rehydrated
    assert "Sarahbe" not in rehydrated
    assert "check with Sarah tomorrow" in rehydrated


def test_anthropic_system_block_list_is_redacted():
    """A system prompt sent as content blocks must not reach the wire intact.

    Anthropic accepts `system` as a bare string or a list of blocks. Only the
    string form was redacted, so the block form left the value in the request
    while the proxy reported redaction as enabled.
    """
    engine = PIIEngine()
    vault = Vault()

    payload = {"system": [{"type": "text", "text": "Contact jane.doe@example.com"}]}
    redacted = engine.redact_payload(payload, vault)

    assert "jane.doe@example.com" not in json.dumps(redacted)


def test_responses_instructions_are_redacted():
    """The Responses API carries caller text outside both `messages` and `input`."""
    engine = PIIEngine()
    vault = Vault()

    payload = {"instructions": "Contact jane.doe@example.com", "input": ""}
    redacted = engine.redact_payload(payload, vault)

    assert "jane.doe@example.com" not in json.dumps(redacted)


def test_responses_input_items_are_redacted():
    """`input` items hold text in `content`, and tool items hold it outside `content`.

    Only bare strings in the `input` list were redacted, so every structured item
    shape the Responses API sends went through untouched.
    """
    engine = PIIEngine()
    vault = Vault()

    payload = {
        "input": [
            {"role": "user", "content": "Contact jane.doe@example.com"},
            {"role": "user", "content": [{"type": "input_text", "text": "Also jane.doe@example.com"}]},
            {"type": "function_call", "name": "send", "arguments": '{"email": "jane.doe@example.com"}'},
            {"type": "function_call_output", "call_id": "c1", "output": "sent to jane.doe@example.com"},
        ]
    }
    redacted = engine.redact_payload(payload, vault)

    assert "jane.doe@example.com" not in json.dumps(redacted)


def test_non_text_blocks_and_unknown_items_survive_redaction():
    """Redaction must not damage image blocks or item types it does not understand."""
    engine = PIIEngine()
    vault = Vault()

    payload = {
        "system": [{"type": "text", "text": "Contact jane.doe@example.com"}, {"type": "image", "source": "s3://x"}],
        "input": [{"type": "unknown_item", "opaque": 42}],
    }
    redacted = engine.redact_payload(payload, vault)

    assert redacted["system"][1] == {"type": "image", "source": "s3://x"}
    assert redacted["input"][0] == {"type": "unknown_item", "opaque": 42}
