"""Anonymous volumetric telemetry dispatcher.

Fire-and-forget async POST to the configured TELEMETRY_ENDPOINT_URL.
Uses the shared httpx.AsyncClient from app state to avoid socket exhaustion.
Falls back to a short-lived client if no shared client is available.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def dispatch_telemetry(
    url: str,
    payload: dict,
    client: Optional[httpx.AsyncClient] = None,
) -> None:
    """Send a volumetric telemetry payload to the configured endpoint.

    Args:
        url:     Destination webhook URL.
        payload: JSON-serialisable dict of anonymous usage metrics.
        client:  Optional shared AsyncClient from app.state. When provided,
                 reuses its connection pool instead of opening a new socket.
                 When absent (e.g. tests), a short-lived client is used.
    """
    try:
        if client is not None and not getattr(client, "is_closed", False):
            await client.post(url, json=payload, timeout=2.0)
        else:
            # Fallback: create a minimal short-lived client (tests / edge cases)
            async with httpx.AsyncClient(timeout=2.0) as _client:
                await _client.post(url, json=payload)
    except Exception as exc:
        # Never crash the data plane for telemetry failures
        logger.debug("Anonymous telemetry dispatch failed (non-fatal): %s", exc)
