"""LiteLLM "Generic Guardrail API" shim for LLM Shield Proxy.

LiteLLM can call *any* guardrail over HTTP using its built-in Generic Guardrail
API, so this needs no PR to LiteLLM and changes no file LiteLLM staff edit. This
module translates LiteLLM's contract onto the guard API LLM Shield already
exposes (``/v1/guard/redact`` and ``/v1/guard/rehydrate``), so nothing new reaches
the vault or the crypto.

It is a shim, not a second gateway: it keeps no vault, no state, and no credential
beyond the one Shield virtual key it uses to call the Shield.

Run it beside the Shield, on a loopback or private interface::

    export SHIELD_BASE_URL=http://127.0.0.1:8000
    export SHIELD_API_KEY=<a virtual key configured on the Shield>
    export LITELLM_GUARDRAIL_SHIM_KEY=<the key LiteLLM sends as x-api-key>
    uvicorn litellm_guardrail_shim:app --host 127.0.0.1 --port 8100

Security posture. This endpoint sits on the request path and speaks about PII, so:

- **Fail closed.** A missing or mismatched ``x-api-key`` is 401, and any Shield
  failure becomes ``BLOCKED`` rather than ``NONE``. Returning ``NONE`` would let
  the request continue and hand the provider exactly the data the guardrail exists
  to withhold.
- **Constant-time key comparison** (``hmac.compare_digest``), matching the Shield's
  own ``/v1/guard`` surface.
- **The shim key is mandatory.** With it unset every request is refused rather than
  the app running open.
- **Bounds mirror the Shield's own** guard-API ceilings (256 texts, 1,000,000
  characters). Invariant 1 applies to this surface exactly as it does to the proxy.
- **No request or response body is ever logged**, and no text is echoed into an
  error message. Invariant 4.
- **Session scope.** The session id is derived from ``litellm_call_id`` and namespaced
  with a ``litellm-`` prefix. See the residual-risk note in
  ``GENERIC_GUARDRAIL_CONTRACT.md``: the Shield scopes a vault by
  ``(session_id, virtual_key_id)``, so every caller of this shim shares one virtual
  key. Use a **dedicated** Shield virtual key for the shim, and treat the shim key
  as equivalent in power to that Shield key.

Not implemented here: streaming restoration. ``GENERIC_GUARDRAIL_CONTRACT.md``
explains why ``stream_holdback_chars`` must be validated against the real
rehydration buffer before it is safe to enable -- a wrong holdback emits a partial
placeholder, the exact failure this guardrail exists to prevent.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Literal, Optional

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("llm_shield.litellm_guardrail_shim")

SHIELD_BASE_URL: str = os.environ.get("SHIELD_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# Virtual key on the Shield. Give the shim its own, so its vault namespace is
# separate from every other client of the Shield.
SHIELD_API_KEY: str = os.environ.get("SHIELD_API_KEY", "")
# What LiteLLM sends as x-api-key (config: litellm_params.api_key).
SHIM_API_KEY: str = os.environ.get("LITELLM_GUARDRAIL_SHIM_KEY", "")

_REDACT_PATH = "/v1/guard/redact"
_REHYDRATE_PATH = "/v1/guard/rehydrate"

# Mirror llm_shield_proxy.api.guard_router's ceilings. Duplicated deliberately: a
# bound that lives only in the thing being called is not a bound on the caller.
MAX_TEXTS_PER_REQUEST = 256
MAX_TOTAL_TEXT_CHARS = 1_000_000
# Bound before the value is used to build a header.
MAX_SESSION_ID_CHARS = 200

_TIMEOUT_SECONDS = 10.0


class GuardrailRequest(BaseModel):
    """The subset of LiteLLM's request this shim acts on.

    Extra fields (``images``, ``tools``, ``tool_calls``, ``request_headers``, ...)
    are accepted and ignored, matching LiteLLM's own lenient parsing. ``texts`` is
    the only channel LiteLLM restores from, so it is the only one translated.
    """

    texts: list[str] = Field(default_factory=list)
    input_type: Literal["request", "response"]
    litellm_call_id: Optional[str] = None


class GuardrailResponse(BaseModel):
    action: Literal["BLOCKED", "NONE", "GUARDRAIL_INTERVENED"]
    blocked_reason: Optional[str] = None
    texts: Optional[list[str]] = None
    # Present for contract completeness. Unused until streaming is validated.
    stream_holdback_chars: Optional[int] = None


app = FastAPI(title="LLM Shield Proxy - LiteLLM generic guardrail shim")


def _authorized(candidate: Optional[str]) -> bool:
    """Constant-time check against the configured shim key.

    Fails closed when no key is configured: an unset secret must never mean
    "open", which is how this kind of shim usually ends up exposed.
    """
    if not SHIM_API_KEY:
        return False
    return hmac.compare_digest(candidate or "", SHIM_API_KEY)


def _session_id(call_id: Optional[str]) -> Optional[str]:
    """Namespaces a LiteLLM call id into a Shield session id.

    The prefix keeps this surface's vaults from colliding with any other client
    that happens to use the same Shield virtual key.
    """
    if not call_id or len(call_id) > MAX_SESSION_ID_CHARS:
        return None
    return f"litellm-{call_id}"


async def _call_shield(path: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Calls one Shield guard endpoint. Raises on any transport or status error."""
    headers = {
        "Authorization": f"Bearer {SHIELD_API_KEY}",
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{SHIELD_BASE_URL}{path}", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Shield returned a non-object payload")
    return result


@app.post("/beta/litellm_basic_guardrail_api")
async def basic_guardrail_api(
    request: GuardrailRequest,
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> Any:
    """LiteLLM's Generic Guardrail API contract.

    ``input_type == "request"`` redacts; anything else restores. Both go through the
    Shield's existing endpoints, so the PII boundary is unchanged by this shim.
    """
    if not _authorized(x_api_key):
        return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

    # Bounds before the payload is copied, forwarded, or used to build a header.
    if len(request.texts) > MAX_TEXTS_PER_REQUEST:
        return JSONResponse(
            status_code=413,
            content={"detail": f"texts exceeds {MAX_TEXTS_PER_REQUEST} entries"},
        )
    if sum(len(text) for text in request.texts) > MAX_TOTAL_TEXT_CHARS:
        return JSONResponse(
            status_code=413,
            content={"detail": f"texts exceeds {MAX_TOTAL_TEXT_CHARS} characters"},
        )

    session_id = _session_id(request.litellm_call_id)
    if session_id is None:
        # Without a call id the reply cannot be restored, and answering NONE would
        # send the caller placeholders. Block, and say why.
        logger.warning("LLM Shield shim: request without a usable litellm_call_id")
        return GuardrailResponse(
            action="BLOCKED",
            blocked_reason=(
                "LLM Shield: no litellm_call_id on the request, so redaction cannot be "
                "correlated with the reply. Blocking rather than returning placeholders."
            ),
        )

    path = _REDACT_PATH if request.input_type == "request" else _REHYDRATE_PATH
    try:
        result = await _call_shield(path, session_id, {"texts": request.texts})
    except Exception:
        # Fail closed, and never log the payload: it is the text under inspection.
        logger.warning("LLM Shield shim: call to %s failed", path)
        return GuardrailResponse(
            action="BLOCKED",
            blocked_reason="LLM Shield is unreachable; blocking the request.",
        )

    replaced = result.get("texts")
    if (
        not isinstance(replaced, list)
        or len(replaced) != len(request.texts)
        or not all(isinstance(item, str) for item in replaced)
    ):
        # LiteLLM replaces `texts` wholesale, so a length or type change has no
        # safe mapping. Refuse rather than guess.
        logger.warning("LLM Shield shim: %s returned an unusable payload shape", path)
        return GuardrailResponse(
            action="BLOCKED",
            blocked_reason="LLM Shield returned an unexpected payload; blocking the request.",
        )

    if replaced == request.texts:
        # Nothing moved. NONE keeps LiteLLM's logs honest about whether the
        # guardrail actually intervened.
        return GuardrailResponse(action="NONE")

    return GuardrailResponse(action="GUARDRAIL_INTERVENED", texts=replaced)


if __name__ == "__main__":  # pragma: no cover - convenience entry point only
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("SHIM_HOST", "127.0.0.1"),
        port=int(os.environ.get("SHIM_PORT", "8100")),
    )
