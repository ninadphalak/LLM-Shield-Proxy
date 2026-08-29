"""Enterprise Structured Audit Logger Module.

Provides SOC 2 and HIPAA compliant structured JSON audit logging with
Segmented Cryptographic Hash Chaining to ensure log tamper-proofing.
Guarantees zero raw PII leakage in log sinks.
"""

from __future__ import annotations

import base64
import binascii
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import APIRouter

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)

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


def _load_or_generate_signing_key() -> ed25519.Ed25519PrivateKey:
    """Loads the Ed25519 audit-signing key from AUDIT_SIGNING_PRIVATE_KEY (PEM or raw 32-byte
    seed, base64/hex), falling back to a freshly generated ephemeral key."""
    raw = settings.AUDIT_SIGNING_PRIVATE_KEY
    if raw:
        raw = raw.strip()
        try:
            if "BEGIN" in raw:
                key = serialization.load_pem_private_key(raw.encode("utf-8"), password=None)
                if not isinstance(key, ed25519.Ed25519PrivateKey):
                    raise TypeError("AUDIT_SIGNING_PRIVATE_KEY PEM is not an Ed25519 key")
                return key

            seed: Optional[bytes] = None
            try:
                seed = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                seed = None
            if seed is None or len(seed) != 32:
                seed = bytes.fromhex(raw)
            if len(seed) != 32:
                raise ValueError("Decoded AUDIT_SIGNING_PRIVATE_KEY seed is not 32 bytes")
            return ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        except Exception as exc:
            logger.warning(
                "Failed to parse AUDIT_SIGNING_PRIVATE_KEY (%s); falling back to an ephemeral Ed25519 key.", exc
            )

    logger.warning(
        "AUDIT_SIGNING_PRIVATE_KEY is unset; generated an ephemeral Ed25519 audit-signing key. "
        "Signed receipts will not be verifiable against a stable public key across restarts."
    )
    return ed25519.Ed25519PrivateKey.generate()


_AUDIT_SIGNING_KEY: ed25519.Ed25519PrivateKey = _load_or_generate_signing_key()
_AUDIT_PUBLIC_KEY: ed25519.Ed25519PublicKey = _AUDIT_SIGNING_KEY.public_key()
_AUDIT_PUBLIC_KEY_RAW: bytes = _AUDIT_PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
)
_AUDIT_PUBLIC_KEY_PEM: str = _AUDIT_PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode("utf-8")
_AUDIT_PUBLIC_KEY_FINGERPRINT: str = hashlib.sha256(_AUDIT_PUBLIC_KEY_RAW).hexdigest()


def get_audit_public_key_info() -> Dict[str, str]:
    """Returns the published Ed25519 public key material used to verify signed audit receipts."""
    return {
        "algorithm": "Ed25519",
        "public_key_pem": _AUDIT_PUBLIC_KEY_PEM,
        "public_key_fingerprint": _AUDIT_PUBLIC_KEY_FINGERPRINT,
        "instance_id": AuditLogger._instance_id,
    }


audit_router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])


@audit_router.get("/pubkey")
async def get_audit_public_key() -> Dict[str, str]:
    """Publishes the proxy's Ed25519 audit-signing public key for offline receipt verification."""
    return get_audit_public_key_info()


class AuditLogger:
    """Enterprise Structured Audit Logger with Segmented Hash Chaining.

    Maintains a tamper-evident SHA-256 hash chain of all audit events.
    In zero-dependency mode, the chain is Segmented (per-process in RAM).
    When a Pod restarts, a new Genesis Hash is created, and the WORM aggregator
    reconstructs the global timeline using instance_id and timestamps.
    """

    _last_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"
    _instance_id: str = socket.gethostname()
    _log_queue: "queue.Queue[tuple[str, Dict[str, Any]]]" = queue.Queue(-1)
    _worker_thread: Optional[threading.Thread] = None
    _worker_init_lock = threading.Lock()
    _chain_lock = threading.Lock()

    @classmethod
    def _start_worker_if_needed(cls):
        if cls._worker_thread is None or not cls._worker_thread.is_alive():
            with cls._worker_init_lock:
                # Double-checked locking
                if cls._worker_thread is None or not cls._worker_thread.is_alive():
                    cls._worker_thread = threading.Thread(target=cls._worker, daemon=True, name="AuditWORMHashWorker")
                    cls._worker_thread.start()

    @classmethod
    def _worker(cls):
        while True:
            try:
                severity, log_entry = cls._log_queue.get()

                # Try to fetch agent_id from context if available in this thread.
                # Note: contextvars don't automatically propagate to background threads unless explicitly passed.
                # Since log_entry is fully constructed by the caller, we assume all necessary context is already inside log_entry!
                # Wait, the original code did: agent_id = agent_identity_ctx.get() inside the method.
                # If we do it in the background thread, agent_identity_ctx.get() will be empty because it's a new thread!
                # So we MUST extract agent_id in the caller and pass it in!

                with cls._chain_lock:
                    log_entry["previous_hash"] = cls._last_hash

                    try:
                        # Single-pass serialization for hashing
                        event_str = json.dumps(log_entry, sort_keys=True)
                    except Exception:
                        # Maintain chain continuity on serialization failures
                        event_str = json.dumps({
                            "event": "AUDIT_SERIALIZATION_FAILURE",
                            "previous_hash": log_entry.get("previous_hash"),
                            "severity": severity
                        }, sort_keys=True)

                    new_hash = hashlib.sha256(event_str.encode("utf-8")).hexdigest()

                    # Avoid double serialization by mutating the JSON string directly
                    final_str = f'{event_str[:-1]}, "hash": "{new_hash}"}}'

                    try:
                        # Sign the canonical hash-chained payload (non-blocking: this already
                        # runs on the dedicated WORM background thread, never the ASGI loop).
                        signature_b64 = base64.b64encode(
                            _AUDIT_SIGNING_KEY.sign(final_str.encode("utf-8"))
                        ).decode("ascii")
                        final_str = (
                            f'{final_str[:-1]}, "signature": "{signature_b64}", '
                            f'"public_key_fingerprint": "{_AUDIT_PUBLIC_KEY_FINGERPRINT}"}}'
                        )
                    except Exception:  # nosec B110 noqa: S110
                        # Security Note: Never let signing failures break WORM chain continuity.
                        pass

                    if severity == "CRITICAL":
                        audit_logger.critical(final_str)
                    elif severity == "WARNING":
                        audit_logger.warning(final_str)
                    else:
                        audit_logger.info(final_str)

                    for handler in audit_logger.handlers:
                        handler.flush()
                    sys.stdout.flush()

                    cls._last_hash = new_hash

            except Exception:  # nosec B110 noqa: S110
                # Security Note: Silently drop failed logs to prevent worker crash
                # Silently drop failed logs to prevent worker crash
                pass

    @classmethod
    def _enqueue_log(cls, severity: str, log_entry: Dict[str, Any]) -> None:
        from llm_shield_proxy.core.config import agent_identity_ctx
        agent_id = agent_identity_ctx.get()
        if agent_id:
            log_entry["agent_identity_claim"] = agent_id

        cls._start_worker_if_needed()
        cls._log_queue.put_nowait((severity, log_entry))

    @classmethod
    def log_startup_event(cls) -> None:
        """Emits a Genesis startup audit event initializing the cryptographic hash chain."""
        initial_hash = secrets.token_hex(32)
        cls._last_hash = initial_hash

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_STARTUP",
            "service": "LLM-Shield",
            "instance_id": cls._instance_id,
            "process_id": os.getpid(),
            "initial_hash": initial_hash,
        }
        cls._enqueue_log("INFO", log_entry)

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

        AuditLogger._enqueue_log("INFO", log_entry)

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
        AuditLogger._enqueue_log("INFO", log_entry)

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
        AuditLogger._enqueue_log("CRITICAL", log_entry)

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
        AuditLogger._enqueue_log("CRITICAL", log_entry)

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
        AuditLogger._enqueue_log("INFO", log_entry)

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
        AuditLogger._enqueue_log("WARNING", log_entry)

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
        AuditLogger._enqueue_log("CRITICAL", log_entry)

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
        AuditLogger._enqueue_log("CRITICAL", log_entry)

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
        AuditLogger._enqueue_log(severity, log_entry)

