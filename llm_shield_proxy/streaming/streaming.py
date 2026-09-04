"""Enterprise Zero-Leakage Streaming & SSE Rehydration Engine.

Implements prefix-free sliding-window buffering for Server-Sent Events (SSE) streams,
using bounded cross-chunk matching for supported bracketed
and realistic synthetic unbracketed entities.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
from typing import Any, AsyncGenerator, AsyncIterator, Optional

import orjson as json

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.observability.tracing import tracer
from llm_shield_proxy.streaming.json_lexer import StreamingJSONLexer

logger = logging.getLogger(__name__)

# Event keys whose values are structural: rewriting them changes what the event MEANS
# rather than what it discloses. An id, a model name or a finish_reason is not PII, and a
# scanner that mangles them breaks clients for no privacy gain.
_SSE_STRUCTURAL_KEYS = frozenset(
    {"id", "object", "model", "role", "type", "finish_reason", "index", "created"}
)

# The ordered content channels, already handled by the retention buffer above. They are
# skipped at EVERY depth, not just the top level: the buffer has already rehydrated the
# caller's own values into them, and re-scanning would redact the caller's data back out
# -- fidelity 1.00 to 0.00 in one line, which is how this was caught.
_CONTENT_KEYS = frozenset({"content", "text"})


def _redact_sibling_strings(node: Any, buffer: "SSERehydrationBuffer", skip_content: bool = True) -> Any:
    """Redact model-originated PII in event fields OTHER than the delta content.

    The content field is the ordered stream and is handled by the retention buffer, which
    reassembles values split across events. Everything else in the event JSON was
    previously forwarded untouched, so a value carried in a sibling field reached the
    client unscanned however obvious it was. Measured at LeakRate 1.00 for that carrier
    by the v2 conformance profile, against LeakRate 0.00 for delta content.

    This is the response-path counterpart of the deep request walk added in 1.5.1: the
    request body is walked recursively, and until now the response was not.

    KNOWN LIMIT, declared rather than hidden: sibling fields are scanned CHUNK-LOCALLY. A
    value split across two events, half in each event's sibling field, is not reassembled
    the way delta content is, because sibling fields are not an ordered stream and moving
    text between events to buffer them would corrupt the event that carries them. The
    conformance profile measures this as a non-zero DeltaFrag on the `sse-json-field`
    carrier, and that number is the honest size of the gap.
    """
    if isinstance(node, dict):
        return {
            key: (
                value
                if key in _SSE_STRUCTURAL_KEYS or (skip_content and key in _CONTENT_KEYS)
                else _redact_sibling_strings(value, buffer, skip_content=skip_content)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_redact_sibling_strings(v, buffer, skip_content=skip_content) for v in node]
    if isinstance(node, str):
        return buffer._redact_model_originated(node)
    return node


class _BoundedOutputCoalescer:
    """Aggregate small output pieces without making write boundaries a memory risk.

    The byte budget governs only aggregation. A single encoded SSE line may be
    larger than the target and is returned directly, because ASGI write boundaries
    are not SSE protocol boundaries and truncating a rehydrated value would corrupt
    the response. The caller separately enforces an absolute per-piece ceiling.
    """

    _MAX_PARTS = 1024

    def __init__(self, byte_budget: int) -> None:
        if byte_budget <= 0:
            raise ValueError("SSE coalescing byte budget must be positive")
        self.byte_budget = byte_budget
        self._parts: list[bytes] = []
        self._size = 0

    def push(self, piece: bytes) -> tuple[bytes, ...]:
        if not piece:
            return ()

        if len(piece) > self.byte_budget:
            if self._parts:
                ready = b"".join(self._parts)
                self._parts.clear()
                self._size = 0
                # At most two references: the bounded aggregate and one
                # indivisible oversized line.
                return (ready, piece)
            # Do not copy, truncate, or split one encoded line merely to satisfy
            # the aggregation target.
            return (piece,)

        if self._parts and (
            self._size + len(piece) > self.byte_budget
            or len(self._parts) >= self._MAX_PARTS
        ):
            ready = b"".join(self._parts)
            self._parts.clear()
            self._parts.append(piece)
            self._size = len(piece)
            return (ready,)

        # Hot path: retain the already-encoded bytes object. No copy and no
        # temporary result collection is allocated before the empty return.
        self._parts.append(piece)
        self._size += len(piece)
        return ()

    def drain(self) -> tuple[bytes, ...]:
        if not self._parts:
            return ()
        ready = b"".join(self._parts)
        self._parts.clear()
        self._size = 0
        return (ready,)


class SSERehydrationBuffer:
    """Sliding-window buffer preventing partial entity token leakage across SSE stream chunks.

    Calculates the exact suffix-to-prefix overlap against active vault tokens,
    holding back incomplete token fragments and flushing upon token completion or stream end.

    Attributes:
        vault: Session-scoped Vault containing active token mappings.
        content_buffer: String accumulator for buffered delta text.
    """

    MAX_TAG_LENGTH: int = 64

    def __init__(self, vault: Vault, max_output_bytes: Optional[int] = None) -> None:
        self.vault: Vault = vault
        self.content_buffer: str = ""
        self.lexer: StreamingJSONLexer = StreamingJSONLexer()
        self.max_output_bytes = max_output_bytes

    def _rehydrate(self, text: str, retention_length: int) -> str:
        # Only the built-in implementation declares the allocation-time cap.
        # Vault subclasses/adapters may preserve the historical two-argument
        # rehydrate contract; the encoded-output boundary below still checks
        # their returned piece before it is queued.
        if getattr(type(self.vault), "rehydrate", None) is Vault.rehydrate:
            return self.vault.rehydrate(
                text,
                retention_length=retention_length,
                max_output_bytes=self.max_output_bytes,
            )
        return self.vault.rehydrate(text, retention_length=retention_length)

    def _response_retention_length(self, text: str) -> int:
        """Characters to hold back so a detectable value cannot straddle the boundary.

        Only used when response-side redaction is on. Holding a fixed-length tail is not
        sufficient by itself: cutting inside a value emits its prefix unredacted, which is
        the chunk-boundary failure this proxy exists to prevent. So the cut moves back to
        the last whitespace inside the window, because every entity this engine detects is
        a whitespace-free run. A window with no whitespace is retained whole.

        The window is bounded on purpose. An unbounded one would be whole-response
        buffering, which removes the leak by removing streaming.
        """
        if not text:
            return 0
        window = min(len(text), settings.RESPONSE_PII_SCAN_WINDOW)
        tail = text[len(text) - window:]
        boundary = tail.rfind(" ")
        return window - boundary - 1 if boundary != -1 else window

    def _redact_model_originated(self, text: str) -> str:
        """Redact PII the model produced, leaving this vault's own tokens alone.

        ORDER IS LOAD-BEARING, and it is the whole difficulty of the response path. The
        text here still holds vault TOKENS, and in SYNTHETIC mode a token is a
        realistic-looking value, so a detector cannot tell "the surrogate we substituted"
        from "PII the model invented" by appearance alone. Redacting after rehydration
        would destroy the caller's own data; redacting without consulting the vault would
        destroy the surrogates and leave nothing to restore. Both mistakes are observable
        in shipping gateways.

        So spans whose matched text is a known token are skipped and everything else is
        replaced. Rehydration then runs on what survives.
        """
        if not text:
            return text
        from llm_shield_proxy.engines.pii_engine import pii_engine

        try:
            spans = pii_engine.detect_spans(text)
        except Exception:  # noqa: BLE001
            # Deliberately NOT fail-closed, unlike the request path. Failing closed here
            # means emitting nothing to the client, which breaks the response for a
            # scanner error rather than for a leak. The text is forwarded and the failure
            # is logged, so the gap is visible rather than silent.
            logger.warning("Response PII scan failed; forwarding unscanned text", exc_info=True)
            return text

        known = getattr(self.vault, "token_to_original", None) or {}
        out = list(text)
        for start, end, entity_type, matched_text in reversed(spans):
            if matched_text in known:
                continue
            out[start:end] = list(f"[{entity_type}_REDACTED]")
        return "".join(out)

    def _calculate_retention_length(self, text: str) -> int:
        """Calculates the minimum trailing retention boundary needed for text.

        Identifies the longest suffix of `text` that forms a strict prefix of
        any token registered in the vault (up to max_token_length - 1).

        Time Complexity: O(T * L) where T is token count and L is max token length.
        Space Complexity: O(1).

        Args:
            text: Current accumulated buffer text.

        Returns:
            Number of trailing characters to retain in the buffer.
        """
        if not text:
            return 0

        max_k = 0
        token_to_original = getattr(self.vault, "token_to_original", None)
        is_word_char = getattr(self.vault, "_is_word_char", None)
        if token_to_original:
            for token in token_to_original:
                # Check prefix lengths up to min(len(text), len(token) - 1)
                limit = min(len(text), len(token) - 1)
                for k in range(limit, max_k, -1):
                    if text.endswith(token[:k]):
                        max_k = k
                        break

                # A COMPLETE token match sitting at the very tail of the buffer is
                # still boundary-ambiguous if the token ends in a word character:
                # more characters may arrive next chunk that extend it into a
                # longer, unrelated word (e.g. token "Maya" + next-chunk "ns" ->
                # legitimate word "Mayans"). Retain the full token until a
                # non-word character (or stream end) resolves the right boundary,
                # instead of rehydrating it prematurely.
                if is_word_char and len(token) <= len(text) and is_word_char(token[-1]) and text.endswith(token):
                    max_k = max(max_k, len(token))

        # Check for partial [ENC_v1_ tokens for StatelessCryptoVault
        if type(self.vault).__name__ == "StatelessCryptoVault":
            last_bracket_idx = text.rfind("[")
            if last_bracket_idx != -1:
                suffix = text[last_bracket_idx:]
                prefix = "[ENC_v1_"
                if prefix.startswith(suffix) or (suffix.startswith(prefix) and "]" not in suffix):
                    max_k = max(max_k, len(suffix))

        return max_k

    def process_delta_text(self, delta_text: str, is_final: bool = False) -> str:
        """Processes incoming delta text chunk and emits safe, rehydrated content.

        Time Complexity: O(N * M) for string rehydration and prefix matching.
        Space Complexity: O(N) where N is the length of the buffer.

        Args:
            delta_text: Incoming incremental text delta from upstream LLM.
            is_final: Flag indicating end of stream or flush signal.

        Returns:
            Safe text slice ready for downstream client consumption.
        """
        # No span here. This runs once per SSE delta; a span here would emit one
        # span per token to any collector. rehydrate_sse_stream opens one span for
        # the whole stream.
        emitted_parts = []

        if delta_text:
            self.content_buffer += delta_text

            # Enforce maximum safety length on the buffer
            if len(self.content_buffer) > 64 * 1024:
                raise ValueError("SSE buffer exceeded maximum safety threshold (backpressure protection)")

            # In a fast-path, empty token_to_original can just skip rehydrate
            token_to_original = getattr(self.vault, "token_to_original", None)
            if (
                token_to_original is not None
                and not token_to_original
                and type(self.vault).__name__ != "StatelessCryptoVault"
            ):
                pass
            else:
                # Calculate dynamic prefix retention bound
                retention_length = self._calculate_retention_length(self.content_buffer)

                if settings.ENABLE_RESPONSE_PII_REDACTION:
                    # Widen the hold-back so a MODEL-originated value cannot straddle the
                    # emit boundary. The vault bound covers only this session's own
                    # tokens, and a value the model invented is not among them.
                    retention_length = max(
                        retention_length,
                        self._response_retention_length(self.content_buffer),
                    )
                    scanned_to = len(self.content_buffer) - retention_length
                    if scanned_to > 0:
                        self.content_buffer = (
                            self._redact_model_originated(self.content_buffer[:scanned_to])
                            + self.content_buffer[scanned_to:]
                        )

                # Apply boundary-aware rehydration up to the retention boundary
                self.content_buffer = self._rehydrate(
                    self.content_buffer, retention_length=retention_length
                )

            # Recalculate retention in case replacements modified the tail
            retention_length = self._calculate_retention_length(self.content_buffer)
            if settings.ENABLE_RESPONSE_PII_REDACTION and not is_final:
                # Redaction rewrote the buffer, so the tail bound has to be recomputed
                # against the new text or an unscanned suffix is emitted.
                retention_length = max(
                    retention_length,
                    self._response_retention_length(self.content_buffer),
                )

            if retention_length == 0 or len(self.content_buffer) <= retention_length:
                if retention_length == 0:
                    emitted_parts.append(self.content_buffer)
                    self.content_buffer = ""
            else:
                emitted = self.content_buffer[:-retention_length]
                self.content_buffer = self.content_buffer[-retention_length:]
                emitted_parts.append(emitted)

        if is_final and self.content_buffer:
            # The retained tail has never been scanned. At end of stream no further
            # context is coming, so scan it now or it leaves the proxy unexamined --
            # which is exactly where a value deliberately placed at the end would sit.
            if settings.ENABLE_RESPONSE_PII_REDACTION:
                self.content_buffer = self._redact_model_originated(self.content_buffer)
            emitted_parts.append(self._rehydrate(self.content_buffer, retention_length=0))
            self.content_buffer = ""

        return "".join(emitted_parts)


async def rehydrate_sse_stream(
    raw_stream: AsyncIterator[bytes],
    vault: Any,
    watermark_text: Optional[str] = None,
    path: str = "v1/chat/completions",
    request_id: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """Asynchronous generator consuming raw SSE bytes and yielding rehydrated SSE chunks.

    Handles fragmented JSON chunks, decodes UTF-8 incrementally, protects against
    Slowloris buffer poisoning, and ensures buffer is flushed before [DONE].

    Time Complexity: O(C) amortized per SSE chunk.
    Space Complexity: O(B), where coalesced writes are bounded by
    MAX_SSE_LINE_LENGTH and one rehydrated output piece is bounded by
    MAX_PAYLOAD_SIZE_BYTES + MAX_SSE_LINE_LENGTH.

    Args:
        raw_stream: Upstream raw byte generator from httpx streaming response.
        vault: Session-scoped Vault for entity rehydration.

    Yields:
        Rehydrated, UTF-8 encoded Server-Sent Events bytes.
    """
    from llm_shield_proxy.security.attestation import StreamDigestReceipt

    session_id = getattr(vault, "session_id", "stateless-session")
    attestation = StreamDigestReceipt(session_id=session_id)

    async def _inner_stream() -> AsyncGenerator[bytes, None]:
        nonlocal watermark_text
        line_accumulator = ""
        client_disconnected = False
        stream_aborted = False
        max_line_length = settings.MAX_SSE_LINE_LENGTH
        # One accepted upstream line can contain a token representing data from
        # one accepted request. Allow that request-bounded value plus the input
        # line's own framing, but fail closed on repeated-token amplification.
        max_output_piece_bytes = settings.MAX_PAYLOAD_SIZE_BYTES + max_line_length
        buffer = SSERehydrationBuffer(vault, max_output_bytes=max_output_piece_bytes)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def _bounded_output(piece: bytes) -> bytes:
            if len(piece) > max_output_piece_bytes:
                raise ValueError("Rehydrated SSE output exceeded maximum safe length")
            return piece

        cached_id = "chatcmpl-watermark"
        cached_object = "chat.completion.chunk"
        cached_created = 0
        cached_model = "unknown"
        is_anthropic_stream = False
        failed_open = False

        canary_token = settings.CANARY_TOKEN if settings.ENABLE_CANARY_TRIPWIRE else None
        canary_tail = ""
        canary_len = len(canary_token) if canary_token else 0

        try:
            async for chunk in raw_stream:
                from llm_shield_proxy.api.main import app_state
                if app_state.is_draining:
                    import logging
                    logging.getLogger("llm_shield").warning("Pod is draining. Aborting stalled stream to emit WORM receipt.")
                    break

                if failed_open:
                    yield chunk
                    continue

                # Lines produced from THIS upstream chunk, emitted in writes no
                # larger than max_line_length unless one indivisible encoded line
                # itself exceeds that aggregation target.
                # An SSE event is two lines (the data line and the blank
                # terminator), so yielding per line cost two ASGI messages and two
                # chunked-transfer frames per event, the second one byte long.
                outgoing = _BoundedOutputCoalescer(max_line_length)

                def _queue_output(piece: bytes) -> tuple[bytes, ...]:
                    return outgoing.push(_bounded_output(piece))

                try:
                    chunk_text = decoder.decode(chunk, final=False)

                    if canary_token:
                        if canary_token in chunk_text or (canary_tail and canary_token in (canary_tail + chunk_text[:canary_len - 1])):
                            import logging

                            from llm_shield_proxy.observability.audit import AuditLogger
                            logging.getLogger("llm_shield").critical("Canary Tripwire triggered in SSE stream. Aborting connection natively.")
                            AuditLogger.log_tripwire_event(
                                session_id=getattr(vault, "session_id", "ephemeral"),
                                path=path,
                                virtual_key_id=getattr(vault, "virtual_key_id", "unknown"),
                                request_id=request_id
                            )
                            break
                        if chunk_text:
                            canary_tail = (canary_tail + chunk_text)[-(canary_len - 1):] if canary_len > 1 else ""

                    line_accumulator += chunk_text

                    if len(line_accumulator) > max_line_length:
                        raise ValueError("Line accumulator exceeded maximum safe length (Slowloris protection)")

                    while "\n" in line_accumulator:
                        line, line_accumulator = line_accumulator.split("\n", 1)
                        stripped = line.strip()

                        if stripped.startswith("event: "):
                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready
                            continue

                        if stripped.startswith("data: ") and stripped != "data: [DONE]":
                            try:
                                json_str = stripped[6:]
                                data_obj = json.loads(json_str)

                                if isinstance(data_obj, dict) and data_obj.get("type") in (
                                    "message_start",
                                    "content_block_delta",
                                    "content_block_start",
                                ):
                                    is_anthropic_stream = True

                                if "id" in data_obj and cached_id == "chatcmpl-watermark":
                                    cached_id = data_obj.get("id", cached_id)
                                    cached_object = data_obj.get("object", cached_object)
                                    cached_created = data_obj.get("created", cached_created)
                                    cached_model = data_obj.get("model", cached_model)

                                # FinOps Stream Usage Extraction
                                if settings.ENABLE_FINOPS_METERING and "usage" in data_obj and isinstance(data_obj["usage"], dict):
                                    usage = data_obj["usage"]
                                    if usage:
                                        prompt_tokens = usage.get("prompt_tokens", 0)
                                        completion_tokens = usage.get("completion_tokens", 0)
                                        total_tokens = usage.get("total_tokens", 0)
                                        model = cached_model
                                        v_id = getattr(vault, "virtual_key_id", "default-tenant")
                                        s_id = getattr(vault, "session_id", None)

                                        def _record_sse_metrics(vk_id: str, mdl: str, p_tok: int, c_tok: int, t_tok: int, sess_id: Optional[str]) -> None:
                                            try:
                                                from llm_shield_proxy.observability.metrics import (
                                                    llm_shield_tokens_total,
                                                )
                                                llm_shield_tokens_total.labels(virtual_key_id=vk_id, model=mdl, type="prompt").inc(p_tok)
                                                llm_shield_tokens_total.labels(virtual_key_id=vk_id, model=mdl, type="completion").inc(c_tok)
                                            except Exception as e:
                                                import logging
                                                logging.getLogger("llm_shield").error(f"Failed to record SSE token metrics: {e}")
                                            from llm_shield_proxy.observability.audit import AuditLogger
                                            AuditLogger.log_finops_metered(sess_id, vk_id, mdl, p_tok, c_tok, t_tok)

                                        if total_tokens > 0:
                                            # Reference retained via app_state.background_tasks so
                                            # this can't be garbage-collected mid-flight.
                                            app_state.spawn_background_task(
                                                asyncio.to_thread(_record_sse_metrics, v_id, model, prompt_tokens, completion_tokens, total_tokens, s_id)
                                            )

                                # 1. OpenAI Chat Completion Delta
                                choices = data_obj.get("choices", [])
                                if choices and isinstance(choices, list):
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta and isinstance(delta["content"], str):
                                        raw_content = delta["content"]
                                        rehydrated_content = buffer.process_delta_text(raw_content)
                                        delta["content"] = rehydrated_content
                                        data_obj["choices"][0]["delta"] = delta
                                        if settings.ENABLE_RESPONSE_PII_REDACTION:
                                            # Sibling fields of the event, which were
                                            # forwarded unscanned until 1.6.0.
                                            data_obj = _redact_sibling_strings(data_obj, buffer)
                                        line = f"data: {json.dumps(data_obj).decode('utf-8')}"
                                # 2. Anthropic Content Block Delta
                                elif "delta" in data_obj and isinstance(data_obj["delta"], dict):
                                    delta = data_obj["delta"]
                                    if "text" in delta and isinstance(delta["text"], str):
                                        raw_content = delta["text"]
                                        rehydrated_content = buffer.process_delta_text(raw_content)
                                        openai_chunk = {
                                            "id": cached_id,
                                            "object": "chat.completion.chunk",
                                            "created": cached_created,
                                            "model": cached_model,
                                            "choices": [
                                                {
                                                    "index": data_obj.get("index", 0),
                                                    "delta": {"content": rehydrated_content},
                                                }
                                            ],
                                        }
                                        line = f"data: {json.dumps(openai_chunk).decode('utf-8')}"
                                    else:
                                        pass  # Skip non-text deltas
                                # 3. Anthropic Content Block Start / Generic text delta
                                elif "content_block" in data_obj and isinstance(data_obj["content_block"], dict):
                                    cb = data_obj["content_block"]
                                    if "text" in cb and isinstance(cb["text"], str):
                                        raw_content = cb["text"]
                                        rehydrated_content = buffer.process_delta_text(raw_content)
                                        openai_chunk = {
                                            "id": cached_id,
                                            "object": "chat.completion.chunk",
                                            "created": cached_created,
                                            "model": cached_model,
                                            "choices": [
                                                {
                                                    "index": data_obj.get("index", 0),
                                                    "delta": {"content": rehydrated_content},
                                                }
                                            ],
                                        }
                                        line = f"data: {json.dumps(openai_chunk).decode('utf-8')}"
                                    else:
                                        pass  # Skip non-text start blocks
                                elif data_obj.get("type") in ("message_stop", "message_delta", "ping"):
                                    pass  # We let [DONE] be handled at stream end
                            except (json.JSONDecodeError, TypeError, KeyError):
                                pass

                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready
                        elif stripped == "data: [DONE]":
                            # Flush the buffer completely BEFORE yielding the [DONE] signal
                            remaining = buffer.process_delta_text("", is_final=True)
                            if remaining:
                                flush_obj = {"choices": [{"delta": {"content": remaining}}]}
                                flush_piece = (
                                    f"data: {json.dumps(flush_obj).decode('utf-8')}\n\n".encode()
                                )
                                for ready in _queue_output(flush_piece):
                                    yield ready

                            if watermark_text:
                                if is_anthropic_stream:
                                    anthropic_chunk = {
                                        "id": cached_id,
                                        "object": "chat.completion.chunk",
                                        "created": cached_created,
                                        "model": cached_model,
                                        "choices": [
                                            {"index": 0, "delta": {"content": watermark_text}, "finish_reason": None}
                                        ],
                                    }
                                    watermark_piece = (
                                        f"data: {json.dumps(anthropic_chunk).decode('utf-8')}\n\n".encode()
                                    )
                                else:
                                    watermark_obj = {
                                        "id": cached_id,
                                        "object": cached_object,
                                        "created": cached_created,
                                        "model": cached_model,
                                        "choices": [
                                            {"index": 0, "delta": {"content": watermark_text}, "finish_reason": None}
                                        ],
                                    }
                                    watermark_piece = (
                                        f"data: {json.dumps(watermark_obj).decode('utf-8')}\n\n".encode()
                                    )
                                for ready in _queue_output(watermark_piece):
                                    yield ready
                                watermark_text = ""  # prevent double yield

                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready
                        else:
                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready

                    for ready in outgoing.drain():
                        yield ready

                except Exception as e:
                    import logging

                    buffered = outgoing.drain()
                    if buffered:
                        # Already rehydrated and safe. These were yielded before
                        # the write coalescing above, so dropping them here would
                        # change behaviour on the failure path rather than only
                        # the framing.
                        for ready in buffered:
                            yield ready

                    if settings.SHIELD_FAILURE_MODE == "FAIL_CLOSED":
                        logging.getLogger(__name__).error(f"Streaming rehydration failed (FAIL_CLOSED): {e}")
                        stream_aborted = True
                        return
                    else:
                        logging.getLogger(__name__).error(f"Streaming rehydration failed (FAIL_OPEN): {e}")
                        failed_open = True
                        if line_accumulator:
                            yield line_accumulator.encode("utf-8")
                        line_accumulator = ""
                        continue

        except (GeneratorExit, asyncio.CancelledError):
            client_disconnected = True
            raise
        finally:
            if not client_disconnected and not failed_open and not stream_aborted:
                trailing_text = decoder.decode(b"", final=True)
                if trailing_text:
                    line_accumulator += trailing_text

                remaining = buffer.process_delta_text("", is_final=True)
                if remaining:
                    flush_obj = {"choices": [{"delta": {"content": remaining}}]}
                    yield f"data: {json.dumps(flush_obj).decode('utf-8')}\n\n".encode()

                if watermark_text:
                    if is_anthropic_stream:
                        anthropic_chunk = {
                            "id": cached_id,
                            "object": "chat.completion.chunk",
                            "created": cached_created,
                            "model": cached_model,
                            "choices": [{"index": 0, "delta": {"content": watermark_text}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(anthropic_chunk).decode('utf-8')}\n\n".encode()
                    else:
                        watermark_obj = {
                            "id": cached_id,
                            "object": cached_object,
                            "created": cached_created,
                            "model": cached_model,
                            "choices": [{"index": 0, "delta": {"content": watermark_text}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(watermark_obj).decode('utf-8')}\n\n".encode()
                    watermark_text = ""

                if line_accumulator:
                    yield line_accumulator.encode("utf-8")

    # One span for the whole stream, replacing one span per delta. Started
    # explicitly rather than as a context manager because this generator yields
    # inside the region, and a context-managed current span across yields leaks
    # span context between tasks.
    flush_span = tracer.start_span("buffer_flush")
    emitted_chunks = 0
    try:
        async for outgoing_chunk in _inner_stream():
            emitted_chunks += 1
            attestation.update(outgoing_chunk)
            yield outgoing_chunk
    finally:
        try:
            flush_span.set_attribute("sse.emitted_chunks", emitted_chunks)
        finally:
            flush_span.end()
        attestation.emit_audit_receipt()
