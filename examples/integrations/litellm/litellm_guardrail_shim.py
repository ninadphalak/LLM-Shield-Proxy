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

Streaming restoration is available, and off unless you ask for it. Set
``SHIM_STREAMING_REHYDRATION=true`` -- and only if LiteLLM's guardrail config also
sets ``streaming_transform_mode: incremental_diff``, since LiteLLM's default
(``block_only``) silently discards a rewriting guardrail's output on the streaming
path. The flag exists because LiteLLM's request body carries no "am I streaming"
field, so the shim cannot tell the two apart; and applying a holdback to a
non-streaming response would withhold a tail that never receives a flush, losing
the value rather than protecting it.

The mapping, validated against ``SSERehydrationBuffer`` in
``tests/integrations/litellm/test_generic_guardrail_shim.py``:

- The Shield's ``/v1/guard/rehydrate/stream`` is called once per round with the
  whole accumulated text and an empty carry, so this shim keeps no per-stream
  state. The buffer's own ``content_buffer`` is the withheld tail, so re-feeding
  the accumulated text reproduces it deterministically.
- It answers ``{"text": emitted, "carry": withheld}``; the shim returns
  ``texts = [emitted + carry]`` with ``stream_holdback_chars = len(carry)``.
- LiteLLM then emits ``text[len(already_emitted) : len(text) - holdback]``, which
  is the newly-safe prefix and never the withheld tail.

That composition is what makes the framework's forward-extension precondition
hold: the withheld region stays raw until it completes, and because it was never
emitted, the restored text that replaces it is still a forward extension of what
the client has already seen.
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
_REHYDRATE_STREAM_PATH = "/v1/guard/rehydrate/stream"

# Opt-in. LiteLLM's request body has no streaming flag, and a holdback applied to a
# non-streaming response withholds a tail that never gets flushed -- so this cannot be
# inferred, only configured alongside `streaming_transform_mode: incremental_diff`.
STREAMING_REHYDRATION: bool = os.environ.get("SHIM_STREAMING_REHYDRATION", "false").strip().lower() == "true"

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


async def _restore_streaming(request: GuardrailRequest, session_id: str) -> GuardrailResponse:
    """Restores one round of an accumulated streaming reply.

    LiteLLM hands over the accumulated text each round and emits
    ``text[len(already_emitted) : len(text) - holdback]`` from whatever we return.
    The Shield's stream endpoint answers ``emitted`` plus the ``carry`` it withheld,
    so returning their concatenation as the text, with ``len(carry)`` as the
    holdback, hands LiteLLM exactly the newly-safe prefix and nothing more.
    """
    restored: list[str] = []
    holdback = 0

    for text in request.texts:
        try:
            # Two calls per round, and the reason is the final flush. LiteLLM forces
            # holdback to 0 on the last round, so whatever is withheld at that point is
            # emitted verbatim -- which means the withheld region has to be the RESTORED
            # text, not the raw token, or every stream ends with the user looking at a
            # placeholder. The stream call supplies the withheld length; the
            # complete-text call supplies the restored text.
            carried = await _call_shield(
                _REHYDRATE_STREAM_PATH,
                session_id,
                {"text": text, "carry": "", "final": False},
            )
            complete = await _call_shield(_REHYDRATE_PATH, session_id, {"texts": [text]})
        except Exception:
            logger.warning("LLM Shield shim: rehydrate call failed")
            return GuardrailResponse(
                action="BLOCKED",
                blocked_reason="LLM Shield is unreachable; blocking the request.",
            )

        carry = carried.get("carry")
        restored_texts = complete.get("texts")
        if (
            not isinstance(carry, str)
            or not isinstance(restored_texts, list)
            or len(restored_texts) != 1
            or not isinstance(restored_texts[0], str)
        ):
            logger.warning("LLM Shield shim: rehydrate returned an unusable payload shape")
            return GuardrailResponse(
                action="BLOCKED",
                blocked_reason="LLM Shield returned an unexpected payload; blocking the request.",
            )

        restored.append(restored_texts[0])
        # The withheld length is measured on the raw text, so applying it to the restored
        # text can only ever withhold MORE than strictly necessary -- which delays a
        # value but never emits one early. One holdback covers every choice in LiteLLM's
        # response, so take the widest.
        holdback = max(holdback, len(carry))

    if restored == request.texts and holdback == 0:
        return GuardrailResponse(action="NONE")
    return GuardrailResponse(action="GUARDRAIL_INTERVENED", texts=restored, stream_holdback_chars=holdback)


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

    if request.input_type != "request" and STREAMING_REHYDRATION:
        return await _restore_streaming(request, session_id)

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
