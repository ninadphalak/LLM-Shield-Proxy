from __future__ import annotations

import json
import time

import pytest

from llm_shield_proxy.compliance.report import verify_worm_log
from llm_shield_proxy.observability.audit import _AUDIT_PUBLIC_KEY_PEM, AuditLogger
from llm_shield_proxy.observability.audit_sink import AuditPersistenceError, JSONLFileAuditSink


@pytest.fixture
def restore_audit_state():
    original = {
        "durability": AuditLogger._durability,
        "sink": AuditLogger._durable_sink,
        "last_hash": AuditLogger._last_hash,
        "sequence": AuditLogger._sequence,
        "chain_id": AuditLogger._chain_id,
        "recovered": AuditLogger._recovered,
    }
    yield
    deadline = time.time() + 3
    while not AuditLogger._log_queue.empty() and time.time() < deadline:
        time.sleep(0.01)
    AuditLogger._durability = original["durability"]
    AuditLogger._durable_sink = original["sink"]
    AuditLogger._last_hash = original["last_hash"]
    AuditLogger._sequence = original["sequence"]
    AuditLogger._chain_id = original["chain_id"]
    AuditLogger._recovered = original["recovered"]


def test_file_sink_rejects_multiline_and_invalid_json(tmp_path):
    sink = JSONLFileAuditSink(str(tmp_path / "audit.jsonl"), fsync=False)

    with pytest.raises(AuditPersistenceError):
        sink.append('{"event":"one"}\n{"event":"two"}')
    with pytest.raises(AuditPersistenceError):
        sink.append("not json")


def test_durable_audit_persists_signed_sequenced_chain(tmp_path, restore_audit_state):
    path = tmp_path / "audit.jsonl"
    AuditLogger._last_hash = "0" * 64
    AuditLogger._sequence = 0
    AuditLogger.configure_durability("durable", str(path), fsync=False)

    AuditLogger.log_security_event("DURABLE_TEST", "INFO", {"result": "allow"})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["sequence"] == 1
    assert records[0]["chain_id"]
    assert records[0]["signature"]
    assert records[0]["details"] == {"result": "allow"}

    summary = verify_worm_log(str(path), _AUDIT_PUBLIC_KEY_PEM)
    assert summary["chain_valid"] is True
    assert summary["signatures_valid"] == 1
    assert summary["first_sequence"] == 1


def test_durable_chain_recovers_tail_after_reconfiguration(tmp_path, restore_audit_state):
    path = tmp_path / "audit.jsonl"
    AuditLogger._last_hash = "0" * 64
    AuditLogger._sequence = 0
    AuditLogger.configure_durability("durable", str(path), fsync=False)
    AuditLogger.log_security_event("FIRST", "INFO", {})
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    AuditLogger.configure_durability("durable", str(path), fsync=False)
    assert AuditLogger._recovered is True
    AuditLogger.log_security_event("SECOND", "INFO", {})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[1]["previous_hash"] == first["hash"]
    assert records[1]["sequence"] == first["sequence"] + 1
    assert records[1]["chain_id"] == first["chain_id"]


def test_required_or_durable_mode_requires_a_path(restore_audit_state):
    with pytest.raises(ValueError):
        AuditLogger.configure_durability("required", None)
    with pytest.raises(ValueError):
        AuditLogger.configure_durability("durable", None)
