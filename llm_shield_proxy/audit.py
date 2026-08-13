import json
import logging
import sys
import hashlib
import socket
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

# Configure structured audit logger for enterprise SOC 2 / HIPAA compliance
audit_logger = logging.getLogger("llm_shield.audit")
audit_logger.setLevel(logging.INFO)

if not audit_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    audit_logger.addHandler(handler)


class AuditLogger:
    """
    Enterprise Structured Audit Logger for SOC 2 / HIPAA Compliance.
    Logs metadata about redaction events without ever leaking raw PII.
    Implements Cryptographic Log Tamper-Proofing (Hash-Chaining).
    """
    _last_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"
    _hash_lock: threading.Lock = threading.Lock()
    _instance_id: str = socket.gethostname()

    @classmethod
    def _compute_and_append_hash(cls, log_entry: Dict[str, Any]) -> None:
        """
        Computes the cryptographic hash chain for the event and appends it.
        """
        event_str = json.dumps(log_entry, sort_keys=True)
        
        with cls._hash_lock:
            hash_payload = (event_str + cls._last_hash).encode('utf-8')
            new_hash = hashlib.sha256(hash_payload).hexdigest()
            log_entry["previous_hash"] = cls._last_hash
            log_entry["hash"] = new_hash
            cls._last_hash = new_hash

    @classmethod
    def log_startup_event(cls) -> None:
        """
        Emits a Genesis Event log on application startup to provide a mathematical
        starting point for WORM-compliant log chains.
        """
        import secrets
        initial_hash = secrets.token_hex(32)
        
        with cls._hash_lock:
            cls._last_hash = initial_hash
            
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_STARTUP",
            "service": "LLM-Shield",
            "instance_id": cls._instance_id,
            "initial_hash": initial_hash
        }
        audit_logger.info(json.dumps(log_entry))
    @staticmethod
    def log_redaction_event(
        session_id: Optional[str],
        entity_counts: Dict[str, int],
        path: str,
        virtual_key_id: str = "BYOK",
        status_code: int = 200
    ):
        """
        Emits a structured JSON audit log event recording entity redaction counts.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PII_REDACTION_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
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
        except ImportError:
            pass

    @staticmethod
    def log_proxy_event(
        session_id: Optional[str],
        path: str,
        method: str,
        virtual_key_id: str = "BYOK",
        status_code: int = 200
    ):
        """
        Emits a structured JSON audit log event for incoming proxy traffic.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_TRAFFIC_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "method": method,
            "status_code": status_code
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))
