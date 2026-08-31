"""Enterprise Structured Audit Logger Module.

Provides privacy-safe structured JSON audit logging with segmented
cryptographic hash chaining and optional durable local persistence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import secrets
import socket
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import APIRouter

from llm_shield_proxy.compliance.evidence import load_ed25519_private_key_material
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.observability.audit_sink import AuditPersistenceError, JSONLFileAuditSink

logger = logging.getLogger(__name__)

# Structured audit logger configuration
audit_logger = logging.getLogger("llm_shield.audit")
audit_logger.setLevel(logging.INFO)

if not audit_logger.handlers:
    import queue
    from logging.handlers import QueueHandler, QueueListener

    # Bounded queue with a strict drop policy: if the stdout sink stalls (slow log
    # collector, container log-driver backpressure, journald hiccup -- all routine
    # ops events, not edge cases), we drop new records instead of growing memory
    # without bound for the life of the process.
    class _BoundedQueueHandler(QueueHandler):
        def enqueue(self, record: logging.LogRecord) -> None:
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                logger.warning(
                    "Audit stdout queue full (sink stalled); dropping log record to bound memory."
                )
                try:
                    from llm_shield_proxy.observability.metrics import audit_events_dropped_total

                    audit_events_dropped_total.labels(sink="stdout_queue").inc()
                except Exception:  # nosec B110 noqa: S110
                    # Security Note: metrics recording must never itself raise inside a drop handler
                    pass

    _log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue(maxsize=10_000)
    _queue_handler = _BoundedQueueHandler(_log_queue)

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
    key_file = settings.AUDIT_SIGNING_KEY_FILE
    if key_file:
        try:
            raw = Path(key_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Unable to read AUDIT_SIGNING_KEY_FILE: {key_file}") from exc
    if raw:
        try:
            return load_ed25519_private_key_material(raw)
        except Exception as exc:
            if key_file:
                raise RuntimeError(f"Invalid Ed25519 key in AUDIT_SIGNING_KEY_FILE: {key_file}") from exc
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

    Chains audit events supplied to this logger using SHA-256 predecessor links.
    In zero-dependency mode, the chain is Segmented (per-process in RAM).
    In best-effort mode, a Pod restart creates a new chain. A configured durable
    sink recovers the previous hash, sequence, and chain identifier.
    """

    _last_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"
    _instance_id: str = socket.gethostname()
    _chain_id: str = str(uuid.uuid4())
    _sequence: int = 0
    _recovered: bool = False
    _durability: str = "best_effort"
    _durable_sink: Optional[JSONLFileAuditSink] = None
    # Bounded: see _enqueue_log for the drop policy applied when this fills up.
    @dataclass
    class _QueueItem:
        severity: str
        log_entry: Dict[str, Any]
        completion: Optional[threading.Event] = None
        error: list[BaseException] = field(default_factory=list)

    _log_queue: "queue.Queue[AuditLogger._QueueItem]" = queue.Queue(maxsize=10_000)
    _worker_thread: Optional[threading.Thread] = None
    _worker_init_lock = threading.Lock()
    _chain_lock = threading.Lock()

    @classmethod
    def configure_durability(
        cls,
        mode: str = "best_effort",
        path: Optional[str] = None,
        *,
        fsync: bool = True,
    ) -> None:
        """Configure an optional append-only durable sink.

        This method exists for startup wiring and tests. Reconfiguration while
        records are actively being emitted is unsupported.
        """
        normalized = mode.lower()
        if normalized not in {"best_effort", "durable", "required"}:
            raise ValueError("AUDIT_DURABILITY must be best_effort, durable, or required")
        if normalized != "best_effort" and not path:
            raise ValueError(f"AUDIT_DURABLE_PATH is required when AUDIT_DURABILITY={normalized}")

        cls._durability = normalized
        cls._durable_sink = None
        cls._recovered = False
        if path:
            resolved_path = path.format(instance_id=cls._instance_id, pid=os.getpid())
            sink = JSONLFileAuditSink(resolved_path, fsync=fsync)
            previous = sink.last_record()
            cls._durable_sink = sink
            if previous:
                previous_hash = previous.get("hash")
                if not previous_hash:
                    raise AuditPersistenceError("Durable audit tail has no hash; refusing unsafe chain recovery")
                cls._last_hash = str(previous_hash)
                cls._sequence = int(previous.get("sequence", 0))
                cls._chain_id = str(previous.get("chain_id") or uuid.uuid4())
                cls._recovered = True

    @classmethod
    def _start_worker_if_needed(cls):
        if cls._worker_thread is None or not cls._worker_thread.is_alive():
            with cls._worker_init_lock:
                # Double-checked locking
                if cls._worker_thread is None or not cls._worker_thread.is_alive():
                    cls._worker_thread = threading.Thread(target=cls._worker, daemon=True, name="AuditHashWorker")
                    cls._worker_thread.start()

    @classmethod
    def _worker(cls):
        while True:
            try:
                item = cls._log_queue.get()
                severity, log_entry = item.severity, item.log_entry

                # agent_identity_claim is extracted from the contextvar by the caller (see
                # _enqueue_log) and embedded into log_entry before it reaches this thread,
                # since contextvars don't propagate across threads.
                with cls._chain_lock:
                    next_sequence = cls._sequence + 1
                    log_entry["chain_id"] = cls._chain_id
                    log_entry["sequence"] = next_sequence
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
                        # runs on the dedicated audit background thread, never the ASGI loop).
                        signature_b64 = base64.b64encode(
                            _AUDIT_SIGNING_KEY.sign(final_str.encode("utf-8"))
                        ).decode("ascii")
                        final_str = (
                            f'{final_str[:-1]}, "signature": "{signature_b64}", '
                            f'"public_key_fingerprint": "{_AUDIT_PUBLIC_KEY_FINGERPRINT}"}}'
                        )
                    except Exception:  # nosec B110 noqa: S110
                        # Security Note: Never let signing failures break hash-chain continuity.
                        pass

                    if cls._durable_sink is not None:
                        cls._durable_sink.append(final_str)

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
                    cls._sequence = next_sequence

                if item.completion is not None:
                    item.completion.set()
                cls._log_queue.task_done()

            except Exception as exc:  # nosec B110 noqa: S110
                logger.exception("Audit worker failed to persist an event")
                try:
                    item.error.append(exc)
                    if item.completion is not None:
                        item.completion.set()
                    cls._log_queue.task_done()
                except Exception:  # nosec B110 noqa: S110
                    pass

    @classmethod
    def _enqueue_log(cls, severity: str, log_entry: Dict[str, Any]) -> None:
        from llm_shield_proxy.core.config import agent_identity_ctx
        agent_id = agent_identity_ctx.get()
        if agent_id:
            log_entry["agent_identity_claim"] = agent_id

        cls._start_worker_if_needed()
        completion = threading.Event() if cls._durability != "best_effort" else None
        item = cls._QueueItem(severity=severity, log_entry=log_entry, completion=completion)
        try:
            if cls._durability == "best_effort":
                cls._log_queue.put_nowait(item)
            else:
                cls._log_queue.put(item, timeout=settings.AUDIT_ENQUEUE_TIMEOUT_SECONDS)
        except queue.Full:
            # Best-effort mode never blocks the request path. If the worker cannot
            # keep up, it drops the event rather than growing memory without bound.
            # audit_events_dropped_total makes sustained drops (i.e. audit records
            # compliance can no longer vouch for) observable and alertable, since a
            # single WARNING line in a high-volume log stream is easy to miss. A
            # Durable modes instead wait for an acknowledgement and surface failures.
            if cls._durability != "best_effort":
                raise AuditPersistenceError("Durable audit queue remained full until its configured timeout")
            logger.warning(
                "Audit queue full (worker stalled); dropping best-effort audit event: event=%s",
                log_entry.get("event", "unknown"),
            )
            try:
                from llm_shield_proxy.observability.metrics import audit_events_dropped_total

                audit_events_dropped_total.labels(sink="worm_chain_queue").inc()
            except Exception:  # nosec B110 noqa: S110
                # Security Note: metrics recording must never itself raise inside a drop handler
                pass

        if completion is not None:
            if not completion.wait(timeout=settings.AUDIT_ENQUEUE_TIMEOUT_SECONDS):
                raise AuditPersistenceError("Required audit persistence acknowledgement timed out")
            if item.error:
                raise AuditPersistenceError("Required audit persistence failed") from item.error[0]

    @classmethod
    def log_startup_event(cls) -> None:
        """Emits a Genesis startup audit event initializing the cryptographic hash chain."""
        initial_hash = cls._last_hash if cls._recovered else secrets.token_hex(32)
        if not cls._recovered:
            cls._last_hash = initial_hash

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "PROXY_RESUME" if cls._recovered else "PROXY_STARTUP",
            "service": "LLM-Shield",
            "instance_id": cls._instance_id,
            "process_id": os.getpid(),
            "initial_hash": initial_hash,
            "audit_durability": cls._durability,
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
    def log_unhandled_exception(
        request_id: Optional[str],
        path: str,
        method: str,
        exc: BaseException,
    ) -> None:
        """Emits a structured JSON audit event recording an unhandled request exception.

        Deliberately carries only the exception's type name -- never `str(exc)` or a
        traceback, since either can contain raw request content (a fragment of an
        unredacted prompt, a malformed value under inspection, etc.) that has no
        business entering the "zero raw PII leakage" WORM audit chain. The full
        exception message and traceback belong in the operational application logger
        (see `global_exception_handler` in api/main.py, which logs both there via
        `logger.error(..., exc_info=exc)`) -- a separate sink with shorter retention
        that engineers use for root-causing, not the compliance-grade audit record.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "UNHANDLED_EXCEPTION",
            "service": "LLM-Shield",
            "instance_id": AuditLogger._instance_id,
            "process_id": os.getpid(),
            "request_id": request_id or "n/a",
            "path": path,
            "method": method,
            "severity": "CRITICAL",
            "exception_type": type(exc).__name__,
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


# Configure once at import/startup. The default remains the historical,
# non-blocking best-effort stdout behavior.
AuditLogger.configure_durability(
    settings.AUDIT_DURABILITY,
    settings.AUDIT_DURABLE_PATH,
    fsync=settings.AUDIT_DURABLE_FSYNC,
)

