"""Application-level rolling digest receipts for emitted SSE chunks.

The receipt is scoped to chunks observed by the response-stream pipeline. It is not a
network-wide proof and does not establish detector recall.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

import orjson as json

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.observability.audit import audit_logger

logger = logging.getLogger(__name__)
_warned_unkeyed = False


class StreamDigestReceipt:
    """Asynchronous rolling SHA-256 digest accumulator.

    Retains a fixed-size digest state and chunk counter rather than buffering
    stream chunks; logger/exporter memory is outside this class.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.hasher = hashlib.sha256()
        self.total_chunks_processed = 0

    def update(self, chunk_bytes: bytes) -> None:
        """Ingest a text chunk into the sequential rolling digest.

        Conceptually:
            h_i = SHA256(chunk_bytes)
            R_i = SHA256(R_{i-1} || h_i)

        Since hasher.update natively acts sequentially, we hash the chunk and update.
        """
        chunk_hash = hashlib.sha256(chunk_bytes).digest()
        self.hasher.update(chunk_hash)
        self.total_chunks_processed += 1

    def emit_audit_receipt(self) -> None:
        """Emit HMAC-signed digest metadata through the configured audit logger.

        Runs after the client already has every byte, so it must not raise: an
        exception here aborted the finished response mid-connection, on every
        stream, under the default configuration (no SHIELD_ENCRYPTION_KEY).
        """
        global _warned_unkeyed
        key_str = settings.SHIELD_ENCRYPTION_KEY
        if not key_str:
            if not _warned_unkeyed:
                _warned_unkeyed = True
                logger.warning(
                    "SHIELD_ENCRYPTION_KEY is unset, so stream digest receipts are not emitted. "
                    "Set it to sign one receipt per streamed response."
                )
            return

        final_digest = self.hasher.hexdigest()

        payload = {
            "event": "stream_digest_receipt",
            "session_id": self.session_id,
            "stream_digest_sha256": final_digest,
            "total_chunks_processed": self.total_chunks_processed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Sign the payload using the explicitly configured SHIELD_ENCRYPTION_KEY.
        # Sort keys to ensure deterministic JSON structure for HMAC verification
        key = key_str.encode("utf-8")
        payload_bytes = json.dumps(payload, option=json.OPT_SORT_KEYS)

        signature = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()
        payload["signature"] = signature

        # Emit the narrowly named receipt through the configured audit logger.
        audit_logger.info(json.dumps(payload).decode("utf-8"))
