import logging
from typing import Optional

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)

def resolve_provider(headers: dict, payload: Optional[dict] = None) -> str:
    """
    Resolves the target upstream provider based on headers and payload.
    1. X-Shield-Provider header
    2. Payload model string inspection
    3. Default from config
    """
    header_provider = headers.get("x-shield-provider") or headers.get("X-Shield-Provider")
    if header_provider:
        return header_provider.lower()

    if payload and isinstance(payload, dict):
        model = payload.get("model", "")
        if isinstance(model, str):
            if "claude" in model.lower() or model.lower().startswith("anthropic/"):
                return "anthropic"

    return settings.DEFAULT_UPSTREAM_PROVIDER.lower()
