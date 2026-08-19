import json
import time
import pytest
from unittest.mock import patch, MagicMock

from llm_shield_proxy.security.fips_kat import run_fips_kat_self_test
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.core.config import settings

def test_fips_kat_success():
    """Test that the FIPS 140-3 KAT passes normally."""
    assert run_fips_kat_self_test() is True

@patch('llm_shield_proxy.security.fips_kat.hashlib.sha256')
def test_fips_kat_sha256_corruption(mock_sha256):
    """Test that intentional corruption of SHA256 KAT fails the test."""
    mock_hash = MagicMock()
    mock_hash.hexdigest.return_value = "bad_hash"
    mock_sha256.return_value = mock_hash
    
    assert run_fips_kat_self_test() is False

@patch('llm_shield_proxy.security.fips_kat.Cipher')
def test_fips_kat_aes_corruption(mock_cipher):
    """Test that intentional corruption of AES-GCM KAT fails the test."""
    mock_cipher_instance = MagicMock()
    mock_encryptor = MagicMock()
    mock_encryptor.update.return_value = b"bad_ciphertext"
    mock_encryptor.finalize.return_value = b""
    mock_encryptor.tag = b"bad_tag"
    mock_cipher_instance.encryptor.return_value = mock_encryptor
    mock_cipher.return_value = mock_cipher_instance
    
    assert run_fips_kat_self_test() is False

def test_audit_logger_rfc6902_diff_and_chaining(caplog):
    """Test RFC 6902 JSON Patch structures and cryptographic hash chaining."""
    settings.AUDIT_LOG_FORMAT = "RFC6902_DIFF"
    
    # Initialize the chain
    AuditLogger.log_startup_event()
    
    patch_ops = [{"op": "replace", "path": "/messages/0/content", "value": "[SSN_1]"}]
    
    start_time = time.perf_counter()
    AuditLogger.log_redaction_event(
        session_id="test_session",
        entity_counts={"SSN": 1},
        path="/v1/chat/completions",
        patch_operations=patch_ops
    )
    duration = (time.perf_counter() - start_time) * 1000  # in ms
    
    assert duration < 1.0, f"Audit logging took {duration} ms, exceeding 1.0 ms budget (target: 0.1ms on prod)"
    
    # Verify log output structure
    logs = [json.loads(record.message) for record in caplog.records if "PII_REDACTION_EVENT" in record.message]
    assert len(logs) == 1
    
    log_entry = logs[0]
    assert "patch_operations" in log_entry
    assert log_entry["patch_operations"] == patch_ops
    assert "hash" in log_entry
    assert "previous_hash" in log_entry
    
    # Verify cryptographic chain integrity
    event_copy = log_entry.copy()
    event_hash = event_copy.pop("hash")
    prev_hash = event_copy.pop("previous_hash")
    
    # Reconstruct the string used for hashing (JSON serialized, keys sorted)
    event_str = json.dumps(event_copy, sort_keys=True)
    hash_payload = (event_str + prev_hash).encode("utf-8")
    expected_hash = __import__('hashlib').sha256(hash_payload).hexdigest()
    
    assert event_hash == expected_hash

