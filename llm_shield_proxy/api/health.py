"""Deep Component Health Probes."""

import asyncio
import logging
import time
from typing import Dict, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.security.vault_client import vault_provider
from llm_shield_proxy.engines.vault import vault_store, RedisVaultStore

logger = logging.getLogger(__name__)

health_router = APIRouter(tags=["Health"])

_readyz_cache: Dict[str, Any] = {}
_CACHE_TTL_SECONDS = 2.0


@health_router.get("/livez")
@health_router.get("/health")
@health_router.get("/healthz")
async def liveness_probe() -> Dict[str, str]:
    """Instant zero-await Liveness probe ensuring the event loop is responsive."""
    return {"status": "ok"}


async def _check_pii_engine() -> bool:
    """Validates local ONNX runtime status and compiled google-re2 rules."""
    try:
        # Check if Tier 1 patterns are loaded
        if not pii_engine._global_strict_profile.tier1_patterns:
            return False
            
        # If Tier 3 ONNX is enabled, verify the session is loaded
        if pii_engine.enable_tier3 and settings.ENABLE_TIER3_ONNX_NER and settings.ONNX_MODEL_PATH:
            if pii_engine._onnx_session is None:
                return False
                
        return True
    except Exception:
        return False


async def _check_vault() -> bool:
    """Validates Vault background rotation is active and populated."""
    try:
        if settings.ENABLE_VAULT_SECRETS:
            # Only consider unhealthy if we haven't successfully fetched secrets
            if not vault_provider._cached_secrets:
                return False
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    """Validates Redis rate limiting backend."""
    try:
        if isinstance(vault_store, RedisVaultStore):
            return await asyncio.wait_for(vault_store.ping_async(), timeout=0.5)
        return True
    except Exception:
        return False


@health_router.get("/readyz")
async def readiness_probe() -> JSONResponse:
    """Asynchronous Readiness check verifying internal sub-system health."""
    current_time = time.monotonic()
    
    # 2-second TTL cache to prevent probe storms from stalling the ASGI event loop.
    # Cold start will skip this because cache is empty.
    if _readyz_cache and _readyz_cache.get("timestamp", 0) > current_time - _CACHE_TTL_SECONDS:
        cached_result = _readyz_cache["result"]
        return JSONResponse(status_code=cached_result["status_code"], content=cached_result["content"])

    # Concurrent execution to avoid blocking
    pii_healthy, vault_healthy, redis_healthy = await asyncio.gather(
        _check_pii_engine(),
        _check_vault(),
        _check_redis(),
        return_exceptions=True
    )

    # Handle exceptions as failures
    pii_healthy = pii_healthy is True
    vault_healthy = vault_healthy is True
    redis_healthy = redis_healthy is True

    components = {
        "pii_engine": "ok" if pii_healthy else "degraded",
        "vault": "ok" if vault_healthy else "degraded",
        "redis": "ok" if redis_healthy else "degraded",
    }

    try:
        from llm_shield_proxy.api.main import APP_VERSION
        version = APP_VERSION
    except ImportError:
        version = "unknown"

    if not all([pii_healthy, vault_healthy, redis_healthy]):
        content = {
            "status": "degraded",
            "service": "llm-shield-proxy",
            "version": version,
            "components": components
        }
        status_code = 503
    else:
        content = {
            "status": "ready",
            "service": "llm-shield-proxy",
            "version": version,
            "components": components
        }
        status_code = 200

    # Populate cache
    result = {"status_code": status_code, "content": content}
    _readyz_cache["timestamp"] = current_time
    _readyz_cache["result"] = result

    return JSONResponse(status_code=status_code, content=content)
