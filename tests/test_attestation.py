import hashlib
import hmac
import json
import logging
import tracemalloc
from collections.abc import AsyncGenerator

import orjson
import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.observability.audit import audit_logger
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream


class LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


@pytest.fixture
def capture_audit_logs():
    handler = LogCaptureHandler()
    audit_logger.addHandler(handler)
    yield handler
    audit_logger.removeHandler(handler)


async def generate_mock_stream(num_chunks: int) -> AsyncGenerator[bytes, None]:
    for i in range(num_chunks):
        data = {"choices": [{"delta": {"content": f" chunk_{i}"}}]}
        yield f"data: {json.dumps(data)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_attestation_log_and_signature(capture_audit_logs, monkeypatch):
    monkeypatch.setattr(settings, "SHIELD_ENCRYPTION_KEY", "test-shield-key")
    vault = Vault(session_id="test_session_123")
    stream = generate_mock_stream(10)

    # Consume stream
    chunks = []
    async for chunk in rehydrate_sse_stream(stream, vault):
        chunks.append(chunk)

    assert len(capture_audit_logs.records) > 0
    # Find the attestation log
    attestation_log = None
    for record in capture_audit_logs.records:
        try:
            data = json.loads(record)
            if data.get("event") == "proof_of_non_egress":
                attestation_log = data
                break
        except json.JSONDecodeError:
            pass

    assert attestation_log is not None
    assert attestation_log["session_id"] == "test_session_123"
    assert attestation_log["total_chunks_processed"] > 0

    # Signature Verification
    signature = attestation_log.pop("signature")
    key_str = getattr(settings, "SHIELD_ENCRYPTION_KEY", None)
    key = key_str.encode("utf-8")

    payload_bytes = orjson.dumps(attestation_log, option=orjson.OPT_SORT_KEYS)
    expected_sig = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()

    assert hmac.compare_digest(signature, expected_sig)


@pytest.mark.asyncio
async def test_attestation_memory_footprint(monkeypatch):
    monkeypatch.setattr(settings, "SHIELD_ENCRYPTION_KEY", "test-shield-key")
    vault = Vault(session_id="test_session_mem")
    num_chunks = 100000
    stream = generate_mock_stream(num_chunks)

    tracemalloc.start()

    # Consume stream (avoid keeping chunks in memory to isolate generator memory)
    async for _ in rehydrate_sse_stream(stream, vault):
        pass

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / (1024 * 1024)
    print(f"\nPeak memory during 100k chunks: {peak_mb:.2f} MB")

    # Target < 25MB for pure generator + hash footprint.
    assert peak < 25 * 1024 * 1024, f"Peak memory exceeded 25MB limit: {peak_mb:.2f} MB"
