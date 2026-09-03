"""Guard API: mask/unmask surface for third-party gateway middleware.

Exposes the redaction and rehydration engines to external proxies (LiteLLM,
Portkey, Kong, Open WebUI) that own their own upstream call and therefore cannot
route traffic through the passthrough proxy mounted at ``/{path:path}``. Those
gateways call out twice per request: once before dispatch to redact, once on the
way back to rehydrate.

Three endpoints:

``POST /v1/guard/redact``
    Batch of texts in, redacted texts out. Registers tokens in the session vault.

``POST /v1/guard/rehydrate``
    Batch of complete (non-streaming) texts in, plaintext restored.

``POST /v1/guard/rehydrate/stream``
    One SSE delta at a time. The caller holds the sliding-window carry-over as an
    opaque string and passes it back on the next call, so the server keeps no
    per-stream state. Retention semantics are exactly those of
    :class:`~llm_shield_proxy.streaming.streaming.SSERehydrationBuffer`: a
    placeholder straddling a chunk boundary is held back rather than emitted in
    fragments.

Security posture. ``/v1/guard/rehydrate`` converts placeholders back into
plaintext PII, which makes it an exfiltration oracle if exposed weakly. It is
gated more strictly than the proxy path: a configured virtual key is mandatory,
and the ``ENABLE_OPEN_BYOK_PASSTHROUGH`` prefix match the proxy honors is
deliberately NOT honored here. Holding an arbitrary provider-shaped key must not
grant the ability to drain a vault.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Dict, List, Optional, Tuple

import orjson
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import vault_store
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer

logger = logging.getLogger(__name__)

guard_router = APIRouter()

# Bounds. Invariant 1 (no unbounded buffering) applies to this surface exactly as
# it does to the proxy path; a gateway is free to send a pathological batch.
MAX_TEXTS_PER_REQUEST = 256
MAX_TOTAL_TEXT_CHARS = 1_000_000

# The carry-over is bounded by the vault's retention window, which is at most
# max_placeholder_length - 1. A caller returning a huge carry is either buggy or
# hostile; either way it must not become an unbounded server-side allocation.
MAX_CARRY_CHARS = 64 * 1024


def _error(status: int, message: str, err_type: str) -> JSONResponse:
    """Builds an OpenAI-shaped error envelope, matching the proxy path."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type}},
    )


def _authenticate(request: Request) -> Tuple[Optional[str], Optional[JSONResponse]]:
    """Resolves the caller to a virtual key id, fail-closed.

    Deliberately stricter than the proxy path in ``api/main.py``. The proxy may
    accept an unrecognized provider-shaped key under
    ``ENABLE_OPEN_BYOK_PASSTHROUGH`` because that key is forwarded upstream and
    the caller is paying for it. Nothing is forwarded here, and
    ``/v1/guard/rehydrate`` returns plaintext PII, so a prefix match is not
    treated as authentication.

    Returns:
        ``(virtual_key_id, None)`` when authenticated, else ``(None, response)``.
    """
    headers = request.headers
    client_auth = headers.get("authorization", "").strip()
    if client_auth.lower().startswith("bearer "):
        client_auth = client_auth[7:].strip()
    if not client_auth:
        client_auth = headers.get("x-api-key", "").strip()
    if not client_auth:
        client_auth = headers.get("x-goog-api-key", "").strip()

    valid_keys = settings.valid_virtual_keys_set

    if valid_keys and client_auth:
        for vk in valid_keys:
            if hmac.compare_digest(client_auth, vk):
                from llm_shield_proxy.api.main import get_virtual_key_id

                return get_virtual_key_id(vk), None

    # Local evaluation escape hatch, same flag the compose examples set. It is an
    # explicit operator decision, unlike a key-shape heuristic.
    if settings.OVERRIDE_CLIENT_AUTH:
        return "anonymous", None

    return None, _error(401, "Invalid Proxy API Key", "authentication_error")


async def _resolve_vault(
    session_id: Optional[str],
    virtual_key_id: str,
    masking_mode_header: Optional[str],
) -> Any:
    """Selects the vault for this session, mirroring the proxy path's selection."""
    from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault
    from llm_shield_proxy.engines.masking import (
        HmacVault,
        MaskingMode,
        ScrubVault,
        resolve_masking_mode,
    )

    masking_mode = resolve_masking_mode(masking_mode_header)

    if masking_mode == MaskingMode.STATELESS_CRYPTO:
        return StatelessCryptoVault()
    if masking_mode == MaskingMode.SCRUB:
        return ScrubVault()
    if masking_mode == MaskingMode.HMAC:
        return HmacVault()

    vault = await vault_store.get_vault_async(session_id, virtual_key_id)
    vault.synthetic = masking_mode != MaskingMode.STRUCTURAL_TAG
    return vault


async def _read_json(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """Parses the request body as a JSON object."""
    try:
        raw = await request.body()
    except Exception:
        return None, _error(400, "Could not read request body", "invalid_request_error")

    try:
        payload = orjson.loads(raw) if raw else {}
    except Exception:
        return None, _error(400, "Parse error: invalid JSON payload", "invalid_request_error")

    if not isinstance(payload, dict):
        return None, _error(400, "Request body must be a JSON object", "invalid_request_error")
    return payload, None


def _extract_texts(payload: Dict[str, Any]) -> Tuple[Optional[List[str]], Optional[JSONResponse]]:
    """Validates and bounds the ``texts`` array."""
    texts = payload.get("texts")
    if not isinstance(texts, list):
        return None, _error(400, "'texts' must be an array of strings", "invalid_request_error")
    if len(texts) > MAX_TEXTS_PER_REQUEST:
        return None, _error(
            413,
            f"'texts' exceeds {MAX_TEXTS_PER_REQUEST} entries",
            "invalid_request_error",
        )

    total = 0
    for item in texts:
        if not isinstance(item, str):
            return None, _error(400, "'texts' must contain only strings", "invalid_request_error")
        total += len(item)
        if total > MAX_TOTAL_TEXT_CHARS:
            return None, _error(
                413,
                f"'texts' exceeds {MAX_TOTAL_TEXT_CHARS} total characters",
                "invalid_request_error",
            )
    return texts, None


@guard_router.post("/v1/guard/redact")
async def guard_redact(
    request: Request,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_shield_masking_mode: Optional[str] = Header(None, alias="X-Shield-Masking-Mode"),
) -> Response:
    """Redacts a batch of texts, registering placeholders in the session vault.

    The session vault is what makes the paired rehydrate call possible, so the
    caller must send a stable ``X-Session-ID`` for the lifetime of one LLM
    request/response pair.
    """
    virtual_key_id, auth_error = _authenticate(request)
    if auth_error is not None:
        return auth_error
    assert virtual_key_id is not None

    payload, parse_error = await _read_json(request)
    if parse_error is not None:
        return parse_error
    assert payload is not None

    texts, text_error = _extract_texts(payload)
    if text_error is not None:
        return text_error
    assert texts is not None

    vault = await _resolve_vault(x_session_id, virtual_key_id, x_shield_masking_mode)
    profile = pii_engine.get_profile(virtual_key_id)

    try:
        redacted = [pii_engine.redact_text(text, vault, active_profile=profile) for text in texts]
    except Exception:
        # Fail closed (invariant 3). Returning the input unchanged on a redaction
        # error would hand the caller raw PII to forward upstream, which is the
        # exact failure this proxy exists to prevent.
        logger.exception("guard.redact failed")
        return _error(500, "Redaction failed", "redaction_error")

    return JSONResponse(content={"texts": redacted})


@guard_router.post("/v1/guard/rehydrate")
async def guard_rehydrate(
    request: Request,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_shield_masking_mode: Optional[str] = Header(None, alias="X-Shield-Masking-Mode"),
) -> Response:
    """Restores plaintext for a batch of complete, non-streaming texts."""
    virtual_key_id, auth_error = _authenticate(request)
    if auth_error is not None:
        return auth_error
    assert virtual_key_id is not None

    payload, parse_error = await _read_json(request)
    if parse_error is not None:
        return parse_error
    assert payload is not None

    texts, text_error = _extract_texts(payload)
    if text_error is not None:
        return text_error
    assert texts is not None

    vault = await _resolve_vault(x_session_id, virtual_key_id, x_shield_masking_mode)

    try:
        # retention_length=0: these are complete texts, so there is no chunk
        # boundary to protect against.
        restored = [vault.rehydrate(text, retention_length=0) for text in texts]
    except Exception:
        logger.exception("guard.rehydrate failed")
        return _error(500, "Rehydration failed", "rehydration_error")

    return JSONResponse(content={"texts": restored})


@guard_router.post("/v1/guard/rehydrate/stream")
async def guard_rehydrate_stream(
    request: Request,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_shield_masking_mode: Optional[str] = Header(None, alias="X-Shield-Masking-Mode"),
) -> Response:
    """Rehydrates one SSE delta, returning the caller's next carry-over window.

    The server holds no per-stream state. ``SSERehydrationBuffer``'s entire state
    is its ``content_buffer`` string, so the caller round-trips it as ``carry``.
    That keeps this endpoint free of a stream registry, TTLs and eviction, and
    lets non-Python gateways (Portkey's TypeScript, Kong's Lua) reuse the same
    retention logic instead of reimplementing it.

    Request:
        ``{"text": str, "carry": str, "final": bool}``
    Response:
        ``{"text": str, "carry": str}`` -- ``text`` is safe to forward to the
        client immediately; ``carry`` must be sent back on the next delta. When
        ``final`` is true the carry is flushed and comes back empty.
    """
    virtual_key_id, auth_error = _authenticate(request)
    if auth_error is not None:
        return auth_error
    assert virtual_key_id is not None

    payload, parse_error = await _read_json(request)
    if parse_error is not None:
        return parse_error
    assert payload is not None

    text = payload.get("text", "")
    carry = payload.get("carry", "")
    is_final = bool(payload.get("final", False))

    if not isinstance(text, str) or not isinstance(carry, str):
        return _error(400, "'text' and 'carry' must be strings", "invalid_request_error")
    if len(carry) > MAX_CARRY_CHARS:
        return _error(413, f"'carry' exceeds {MAX_CARRY_CHARS} characters", "invalid_request_error")
    if len(text) > MAX_TOTAL_TEXT_CHARS:
        return _error(
            413, f"'text' exceeds {MAX_TOTAL_TEXT_CHARS} characters", "invalid_request_error"
        )

    vault = await _resolve_vault(x_session_id, virtual_key_id, x_shield_masking_mode)

    buffer = SSERehydrationBuffer(vault)
    buffer.content_buffer = carry

    try:
        emitted = buffer.process_delta_text(text, is_final=is_final)
    except ValueError as ve:
        # Backpressure ceiling or output-bound breach. Both are fail-closed by
        # design; do not emit the buffer.
        return _error(413, str(ve), "invalid_request_error")
    except Exception:
        logger.exception("guard.rehydrate_stream failed")
        return _error(500, "Rehydration failed", "rehydration_error")

    return JSONResponse(content={"text": emitted, "carry": buffer.content_buffer})
