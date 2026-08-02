"""
Anonymous Volumetric Telemetry System for LLM-Shield.

PRIVACY & ZERO-EGRESS GUARANTEE:
Telemetry in LLM-Shield is strictly OPT-IN ('Bring Your Own Database').
By default, TELEMETRY_ENABLED=false and no data egress occurs.

When explicitly enabled by enterprise operators via environment configuration,
this worker tracks ONLY purely anonymous, aggregated volumetric metrics
(such as total requests processed, redaction counts, active proxy connections, and timestamp).
NO Personally Identifiable Information (PII), prompts, responses, payload contents,
or IP addresses are EVER collected, logged, or transmitted.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import httpx
from app.config import settings

logger = logging.getLogger("llm_shield.telemetry")


class TelemetryTracker:
    """
    Background worker tracking anonymous volumetric metrics for LLM-Shield instances
    and periodically sending aggregated metrics to an enterprise-configured telemetry REST API.
    """

    def __init__(self):
        self.instance_id: str = str(uuid.uuid4())
        self.total_requests: int = 0
        self.total_redactions: int = 0
        self.active_connections: int = 0
        self._background_task: Optional[asyncio.Task] = None

    @property
    def is_enabled(self) -> bool:
        """
        Returns True ONLY if telemetry is explicitly enabled AND endpoint & keys are configured.
        """
        return bool(
            settings.TELEMETRY_ENABLED
            and settings.TELEMETRY_ENDPOINT_URL
            and settings.TELEMETRY_API_KEY
        )

    def record_request(self, redactions_count: int = 0):
        """
        Increments anonymous volumetric request and redaction counters if telemetry is enabled.
        """
        if not self.is_enabled:
            return
        self.total_requests += 1
        self.total_redactions += redactions_count

    def increment_active(self):
        if not self.is_enabled:
            return
        self.active_connections += 1

    def decrement_active(self):
        if not self.is_enabled:
            return
        self.active_connections = max(0, self.active_connections - 1)

    def get_metrics(self) -> Dict[str, Any]:
        """
        Returns snapshot of current anonymous volumetric metrics.
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_proxy_connections": self.active_connections,
            "total_requests_processed": self.total_requests,
            "total_pii_redactions": self.total_redactions,
            "instance_id": self.instance_id,
            "telemetry_enabled": self.is_enabled,
        }

    async def emit_telemetry(self):
        """
        Sends aggregated volumetric metrics to configured telemetry REST API endpoint.
        Returns early if telemetry is disabled or unconfigured.
        FAILS SILENTLY on any network error so main proxy traffic is NEVER impacted.
        """
        if not self.is_enabled:
            return

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_proxy_connections": self.active_connections,
            "total_requests_processed": self.total_requests,
            "total_pii_redactions": self.total_redactions,
        }

        headers = {
            "apikey": settings.TELEMETRY_API_KEY,
            "Authorization": f"Bearer {settings.TELEMETRY_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(
                    settings.TELEMETRY_ENDPOINT_URL,
                    json=payload,
                    headers=headers
                )
                if res.status_code >= 400:
                    logger.error(f"Telemetry REST HTTP Error {res.status_code}: {res.text}")
        except Exception as exc:
            # Critical Safety: log error locally, fail silently, never crash main proxy pipeline
            logger.error(f"Telemetry emission failed silently: {exc}")

    async def _heartbeat_worker(self):
        """
        Periodic background task emitting aggregated telemetry.
        """
        while self.is_enabled:
            try:
                await asyncio.sleep(60)  # Emission heartbeat interval
                await self.emit_telemetry()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Telemetry heartbeat loop error: {exc}")

    def start(self):
        if self.is_enabled and self._background_task is None:
            try:
                loop = asyncio.get_running_loop()
                self._background_task = loop.create_task(self._heartbeat_worker())
            except RuntimeError:
                pass

    def stop(self):
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()


telemetry_tracker = TelemetryTracker()
