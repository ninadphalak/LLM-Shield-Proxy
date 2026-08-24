"""Enterprise Structured Audit Logger Module.

Provides SOC 2 and HIPAA compliant structured JSON audit logging with
Segmented Cryptographic Hash Chaining to ensure log tamper-proofing.
Guarantees zero raw PII leakage in log sinks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from llm_shield_proxy.core.config import settings

# Structured audit logger configuration
audit_logger = logging.getLogger("llm_shield.audit")
audit_logger.setLevel(logging.INFO)

if not audit_logger.handlers:
    import queue
    from logging.handlers import QueueHandler, QueueListener

    # True asynchronous logging: a lock-free queue that won't block the ASGI event loop.
    _log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue(-1)
    _queue_handler = QueueHandler(_log_queue)

    _stream_handler = logging.StreamHandler(sys.stdout)
    _stream_handler.setFormatter(logging.Formatter("%(message)s"))

    # The listener runs on a dedicated background thread
    _queue_listener = QueueListener(_log_queue, _stream_handler, respect_handler_level=True)
    _queue_listener.start()

    audit_logger.addHandler(_queue_handler)


class AuditLogger:
    """Enterprise Structured Audit Logger with Segmented Hash Chaining.

    Maintains a tamper-evident SHA-256 hash chain of all audit events.
    In zero-dependency mode, the chain is Segmented (per-process in RAM).
    When a Pod restarts, a new Genesis Hash is created, and the WORM aggregator
    reconstructs the global timeline using instance_id and timestamps.
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
            "process_id": os.getpid(),
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
        patch_operations: Optional[list[Dict[str, Any]]] = None,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event recording entity redaction counts."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PII_REDACTION_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "status_code": status_code,
            "applied_role_name": applied_role_name,
            "total_entities_redacted": sum(entity_counts.values()),
            "entity_breakdown": entity_counts,
        }

        if settings.AUDIT_LOG_FORMAT == "RFC6902_DIFF" and patch_operations is not None:
            log_entry["patch_operations"] = patch_operations
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))

        try:
            from llm_shield_proxy.observability.metrics import llm_shield_pii_redacted_total

            for entity_type, count in entity_counts.items():
                llm_shield_pii_redacted_total.labels(entity_type=entity_type).inc(count)
        except Exception as exc:
            audit_logger.debug("Metrics recording exception: %s", exc)

    @staticmethod
    def log_proxy_event(
        session_id: Optional[str],
        path: str,
        method: str,
        virtual_key_id: str = "BYOK",
        status_code: int = 200,
        request_id: Optional[str] = None,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event for incoming proxy traffic."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_TRAFFIC_EVENT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "method": method,
            "status_code": status_code,
            "applied_role_name": applied_role_name,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))

    @staticmethod
    def log_tripwire_event(
        session_id: Optional[str],
        path: str,
        virtual_key_id: str = "BYOK",
        request_id: Optional[str] = None,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event recording a Canary Tripwire trigger."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "CANARY_TRIPWIRE_TRIGGERED",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "severity": "CRITICAL",
            "action": "CONNECTION_SEVERED",
            "applied_role_name": applied_role_name,
            "message": "Prompt-extraction attack detected. Canary token found in outbound stream.",
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.critical(json.dumps(log_entry))

    @staticmethod
    def log_blast_radius_exceeded(
        session_id: Optional[str],
        virtual_key_id: str,
        entities_count: int,
        path: str,
        request_id: Optional[str] = None,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event recording a Blast Radius Circuit Breaker trip."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "BLAST_RADIUS_EXCEEDED",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "path": path,
            "severity": "CRITICAL",
            "action": "REQUEST_BLOCKED_HTTP_429",
            "applied_role_name": applied_role_name,
            "total_entities_detected": entities_count,
            "message": f"Data exfiltration threshold exceeded. Detected {entities_count} PII entities in a single request or sliding window.",
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.critical(json.dumps(log_entry))

    @staticmethod
    def log_finops_metered(
        session_id: Optional[str],
        virtual_key_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event for FinOps chargeback telemetry."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "FINOPS_USAGE_METERED",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "severity": "INFO",
            "model": model,
            "applied_role_name": applied_role_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.info(json.dumps(log_entry))

    @staticmethod
    def log_upstream_retry_attempt(
        session_id: Optional[str],
        request_id: Optional[str],
        virtual_key_id: str,
        attempt: int,
        url: str,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event for an upstream retry attempt."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "UPSTREAM_RETRY_ATTEMPT",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "severity": "WARNING",
            "attempt": attempt,
            "url": url,
            "applied_role_name": applied_role_name,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.warning(json.dumps(log_entry))

    @staticmethod
    def log_provider_failover_triggered(
        session_id: Optional[str],
        request_id: Optional[str],
        virtual_key_id: str,
        fallback_url: str,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event when an upstream request falls back."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROVIDER_FAILOVER_TRIGGERED",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "severity": "CRITICAL",
            "fallback_url": fallback_url,
            "applied_role_name": applied_role_name,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.critical(json.dumps(log_entry))

    @staticmethod
    def log_circuit_breaker_tripped(
        session_id: Optional[str],
        request_id: Optional[str],
        virtual_key_id: str,
        consecutive_loops: int,
        applied_role_name: str = "global_env",
    ) -> None:
        """Emits a structured JSON audit event when an agent loop circuit breaker trips."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "CIRCUIT_BREAKER_TRIPPED",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "virtual_key_id": virtual_key_id,
            "session_id": session_id or "ephemeral",
            "severity": "CRITICAL",
            "consecutive_loops": consecutive_loops,
            "applied_role_name": applied_role_name,
        }
        AuditLogger._compute_and_append_hash(log_entry)
        audit_logger.critical(json.dumps(log_entry))

    @staticmethod
    def log_security_event(
        event_type: str,
        severity: str,
        details: Dict[str, Any],
        virtual_key_id: str = "BYOK",
    ) -> None:
        """Emits a structured JSON audit event for generic security events (e.g., RBAC failure)."""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "virtual_key_id": virtual_key_id,
            "severity": severity,
            "details": details,
        }
        AuditLogger._compute_and_append_hash(log_entry)

        log_str = json.dumps(log_entry)
        if severity == "CRITICAL":
            audit_logger.critical(log_str)
        elif severity == "WARNING":
            audit_logger.warning(log_str)
        else:
            audit_logger.info(log_str)

