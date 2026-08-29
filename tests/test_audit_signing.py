"""Ed25519 Cryptographic Audit Receipt Test Suite.

Validates that WORM-chained audit events are signed with Ed25519, that the
public key is published for offline verification, and that tampering with a
signed receipt is cryptographically detectable.
"""

import base64
import json
import time

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.observability.audit import (
    _AUDIT_PUBLIC_KEY_FINGERPRINT,
    _AUDIT_PUBLIC_KEY_PEM,
    AuditLogger,
    get_audit_public_key_info,
)

client = TestClient(app)


def _reconstruct_canonical_bytes(record: dict, exclude: tuple) -> bytes:
    # audit.py appends "hash"/"signature"/"public_key_fingerprint" to an already-sorted
    # JSON string rather than re-serializing, so reconstruction must preserve on-disk key
    # order (as produced by json.loads) rather than re-sorting keys.
    stripped = {k: v for k, v in record.items() if k not in exclude}
    return json.dumps(stripped).encode("utf-8")


def _wait_for_event(caplog, event_name: str, timeout_seconds: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for record in caplog.records:
            if event_name in record.message:
                return json.loads(record.message)
        time.sleep(0.05)
    pytest.fail(f"Signed audit receipt for {event_name} was not emitted in time")


def test_get_audit_public_key_info_shape():
    info = get_audit_public_key_info()
    assert info["algorithm"] == "Ed25519"
    assert "BEGIN PUBLIC KEY" in info["public_key_pem"]
    assert len(info["public_key_fingerprint"]) == 64


def test_pubkey_endpoint_matches_module_key():
    response = client.get("/api/v1/audit/pubkey")
    assert response.status_code == 200
    data = response.json()
    assert data["algorithm"] == "Ed25519"
    assert data["public_key_pem"] == _AUDIT_PUBLIC_KEY_PEM
    assert data["public_key_fingerprint"] == _AUDIT_PUBLIC_KEY_FINGERPRINT


def test_security_event_emits_valid_signed_receipt(caplog):
    AuditLogger.log_security_event(
        event_type="TEST_SIGNED_EVENT",
        severity="INFO",
        details={"probe": "ed25519-signature-test"},
    )

    record = _wait_for_event(caplog, "TEST_SIGNED_EVENT")

    assert "hash" in record
    assert "signature" in record
    assert record["public_key_fingerprint"] == _AUDIT_PUBLIC_KEY_FINGERPRINT

    public_key = load_pem_public_key(_AUDIT_PUBLIC_KEY_PEM.encode("utf-8"))
    canonical = _reconstruct_canonical_bytes(record, exclude=("signature", "public_key_fingerprint"))
    public_key.verify(base64.b64decode(record["signature"]), canonical)


def test_tampered_receipt_fails_signature_verification(caplog):
    AuditLogger.log_security_event(
        event_type="TEST_TAMPER_EVENT",
        severity="INFO",
        details={"probe": "tamper-detection"},
    )

    record = _wait_for_event(caplog, "TEST_TAMPER_EVENT")

    # Simulate a tampered log store: an attacker mutates the payload post-hoc.
    record["details"] = {"probe": "malicious-injection"}

    public_key = load_pem_public_key(_AUDIT_PUBLIC_KEY_PEM.encode("utf-8"))
    canonical = _reconstruct_canonical_bytes(record, exclude=("signature", "public_key_fingerprint"))

    with pytest.raises(InvalidSignature):
        public_key.verify(base64.b64decode(record["signature"]), canonical)
