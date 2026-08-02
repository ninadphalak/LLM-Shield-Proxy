import json
import logging
import sys
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
    """

    @staticmethod
    def log_redaction_event(
        session_id: Optional[str],
        entity_counts: Dict[str, int],
        path: str,
        status_code: int = 200
    ):
        """
        Emits a structured JSON audit log event recording entity redaction counts.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PII_REDACTION_EVENT",
            "service": "LLM-Shield",
            "session_id": session_id or "ephemeral",
            "path": path,
            "status_code": status_code,
            "total_entities_redacted": sum(entity_counts.values()),
            "entity_breakdown": entity_counts,
        }
        audit_logger.info(json.dumps(log_entry))

    @staticmethod
    def log_proxy_event(
        session_id: Optional[str],
        path: str,
        method: str,
        status_code: int = 200
    ):
        """
        Emits a structured JSON audit log event for incoming proxy traffic.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_TRAFFIC_EVENT",
            "service": "LLM-Shield",
            "session_id": session_id or "ephemeral",
            "path": path,
            "method": method,
            "status_code": status_code
        }
        audit_logger.info(json.dumps(log_entry))
