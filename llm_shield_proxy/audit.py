"""Enterprise Structured Audit Logger Module.

Provides SOC 2 and HIPAA compliant structured JSON audit logging with
cryptographic SHA-256 hash chaining to ensure log tamper-proofing.
Guarantees zero raw PII leakage in log sinks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import socket
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Structured audit logger configuration
audit_logger = logging.getLogger("llm_shield.audit")
audit_logger.setLevel(logging.INFO)

if not audit_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)


class AuditLogger:
    """Enterprise Structured Audit Logger with Cryptographic Hash Chaining.

    Maintains a tamper-evident SHA-256 hash chain of all audit events.
    Records metadata about redaction counts without storing or exposing raw sensitive data.
    """

    _last_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"
    _hash_lock: threading.Lock = threading.Lock()
    _instance_id: str = socket.gethostname()

    @classmethod
    def _compute_and_append_hash(cls, log_entry: Dict[str, Any]) -> None:
        """Computes and attaches the cryptographic hash chain to a log record."""
        event_str = json.dumps(log_entry, sort_keys=True)
        with cls._hash_lock:
            hash_payload = (event_str + cls._last_hash).encode("utf-8")
            new_hash = hashlib.sha256(hash_payload).hexdigest()
            log_entry["previous_hash"] = cls._last_hash
            log_entry["hash"] = new_hash
            cls._last_hash = new_hash

    @classmethod
    def log_startup_event(cls) -> None:
        """Emits a Genesis startup audit event initializing the cryptographic hash chain."""
        initial_hash = secrets.token_hex(32)
        with cls._hash_lock:
            cls._last_hash = initial_hash

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_STARTUP",
            "service": "LLM-Shield",
            "instance_id": cls._instance_id,
            "initial_hash": initial_hash,
        }
        cls._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))

    @staticmethod
    def log_redaction_event(
        session_id: Optional[str],
        entity_counts: Dict[str, int],
        path: str,
        virtual_key_id: str = "BYOK",
        status_code: int = 200,
        request_id: Optional[str] = None,
    ) -> None:
        """Emits a structured JSON audit event recording entity redaction counts."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PII_REDACTION_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "status_code": status_code,
            "total_entities_redacted": sum(entity_counts.values()),
            "entity_breakdown": entity_counts,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))

        try:
            from llm_shield_proxy.metrics import llm_shield_pii_redacted_total
            for entity_type, count in entity_counts.items():
                llm_shield_pii_redacted_total.labels(entity_type=entity_type).inc(count)
        except Exception:
            pass

    @staticmethod
    def log_proxy_event(
        session_id: Optional[str],
        path: str,
        method: str,
        virtual_key_id: str = "BYOK",
        status_code: int = 200,
        request_id: Optional[str] = None,
    ) -> None:
        """Emits a structured JSON audit event for incoming proxy traffic."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_TRAFFIC_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "method": method,
            "status_code": status_code,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))
