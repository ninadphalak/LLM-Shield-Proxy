"""Enterprise Zero-Leakage Streaming & SSE Rehydration Engine.

Implements prefix-free sliding-window buffering for Server-Sent Events (SSE) streams,
using bounded cross-chunk matching for supported bracketed
and realistic synthetic unbracketed entities.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
from collections import Counter
from typing import Any, AsyncGenerator, AsyncIterator, Dict, Iterator, Optional

import orjson as json

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.observability.tracing import tracer
from llm_shield_proxy.streaming.json_lexer import StreamingJSONLexer

logger = logging.getLogger(__name__)

class StreamCapacityExceeded(ValueError):
    """A deliberate safety limit was hit, which must never be forgiven by FAIL_OPEN."""


# Event keys whose values are structural.
_SSE_STRUCTURAL_KEYS = frozenset(
    {"id", "object", "model", "role", "type", "finish_reason", "index", "created"}
)

# The ordered channels the retention buffer owns.
_BUFFERED_CHANNEL_KEYS = frozenset({"content", "text", "arguments"})

# How many retention windows one stream may open.
MAX_STREAM_WINDOWS = 256

# How many distinct sibling JSON paths one stream may keep a cross-event tail for.
MAX_SIBLING_PATHS = 64

# How many distinct Anthropic content-block indices one stream may hand ordinals to.
MAX_ANTHROPIC_TOOL_ORDINALS = 256

# A retention window's identity: `(choice index, tool-call index or None)`.
_WindowKey = tuple


def _entry_index(entry: Any) -> int:
    """The `index` an SSE choice or tool-call delta is matched by across events."""
    raw = entry.get("index", 0) if isinstance(entry, dict) else 0
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return 0


def _list_entry_identity(item: Any, position: int) -> str:
    """Identifies one list entry so its path stays the same across events."""
    if isinstance(item, dict):
        raw = item.get("index")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            return f"i{raw}"
    return f"p{position}"


def _tool_argument_fragments(delta: Any) -> Iterator[tuple]:
    """Yields `(tool-call index, function object)` for each argument fragment in a delta."""
    for entry in (delta.get("tool_calls") if isinstance(delta, dict) else None) or ():
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        if isinstance(function, dict) and isinstance(function.get("arguments"), str):
            yield _entry_index(entry), function


def _json_string_body(value: str) -> str:
    """`value` as it must appear INSIDE a JSON string, without the quotes."""
    return json.dumps(value).decode("utf-8")[1:-1]


class _JsonStringVaultView:
    """A vault whose restored values are escaped for a JSON string context."""

    # Bound as a class attribute so `type(vault).rehydrate is Vault.rehydrate` still
    # holds and `SSERehydrationBuffer._rehydrate` keeps passing its byte ceiling.
    rehydrate = Vault.rehydrate

    def __init__(self, vault: Any) -> None:
        self._vault = vault
        self._escaped: Dict[str, str] = {}
        self._escaped_from = -1

    def __getattr__(self, name: str) -> Any:
        # Everything else -- the lock, session id, max_token_length -- is the real vault's.
        return getattr(self._vault, name)

    @property
    def token_to_original(self) -> Dict[str, str]:
        source = getattr(self._vault, "token_to_original", None) or {}
        # Rebuilt only when the vault gained tokens, so this stays off the per-delta
        # path: the response phase does not mint tokens (invariant 2).
        if len(source) != self._escaped_from:
            self._escaped = {token: _json_string_body(original) for token, original in source.items()}
            self._escaped_from = len(source)
        return self._escaped


def json_escaped_vault(vault: Any) -> Any:
    """A vault whose restored values are escaped for a JSON string context."""
    return _JsonStringVaultView(vault)


def _append_tool_arguments(delta: dict, tool_index: int, text: str) -> None:
    """Appends flushed `arguments` text to a delta, on the tool call that owns it."""
    entries = delta.get("tool_calls")
    if not isinstance(entries, list):
        entries = []
        delta["tool_calls"] = entries
    for entry in entries:
        if not isinstance(entry, dict) or _entry_index(entry) != tool_index:
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        # Appended rather than assigned: a fragment for this call may already have been
        # restored into this same event, and the flush belongs after it.
        function["arguments"] = f"{function.get('arguments', '')}{text}"
        return
    entries.append({"index": tool_index, "function": {"arguments": text}})


def redact_model_originated_text(text: str, vault: "Vault") -> str:
    """Redact PII the model produced, leaving this vault's own tokens alone."""
    if not text:
        return text
    from llm_shield_proxy.engines.pii_engine import normalize_and_desmuggle, pii_engine

    try:
        spans = pii_engine.detect_spans(text)
    except Exception:  # noqa: BLE001
        # Deliberately NOT fail-closed, unlike the request path. Failing closed here
        # means emitting nothing to the client, which breaks the response for a
        # scanner error rather than for a leak. The text is forwarded and the failure
        # is logged, so the gap is visible rather than silent.
        logger.warning("Response PII scan failed; forwarding unscanned text", exc_info=True)
        return text

    known = getattr(vault, "token_to_original", None) or {}

    def _apply(source: str, found: list) -> str:
        result = list(source)
        for start, end, entity_type, matched_text in reversed(found):
            if matched_text in known:
                continue
            result[start:end] = list(f"[{entity_type}_REDACTED]")
        return "".join(result)

    redacted = _apply(text, spans)

    # The request path normalizes before scanning; this path did not, so a zero-width
    # space or a fullwidth `@` inside a value passed straight through. Normalizing here
    # unconditionally is not an option because it shifts the offsets the spans point at.
    #
    # So: scan the raw text, then scan the normalized form as well. If normalizing
    # reveals PII the raw scan missed, the normalized-and-redacted text is what the
    # client gets. Text with nothing hidden in it takes neither branch and is returned
    # byte for byte.
    normalized = normalize_and_desmuggle(text)
    if normalized != text:
        try:
            normalized_spans = pii_engine.detect_spans(normalized)
        except Exception:  # noqa: BLE001
            logger.warning("Normalized response scan failed; forwarding raw scan", exc_info=True)
            return redacted
        # Compare OCCURRENCE COUNTS, not membership. The test used to be "does this
        # value appear anywhere in the raw text", which a duplicate defeats: a response
        # carrying both `bob@example.com` and its fullwidth-@ twin made that true for
        # every normalized match, so the raw result was returned and the obfuscated copy
        # reached the client, where it renders as an ordinary address.
        #
        # Normalization revealing something means strictly MORE matches of a value than
        # the raw scan found, which is true for the duplicate case and false for text
        # with nothing hidden in it.
        raw_counts = Counter(span[3] for span in spans if span[3] not in known)
        normalized_counts = Counter(
            span[3] for span in normalized_spans if span[3] not in known
        )
        if any(count > raw_counts[value] for value, count in normalized_counts.items()):
            return _apply(normalized, normalized_spans)

    return redacted


def redact_model_originated_tree(node: Any, vault: "Vault", skip_keys: frozenset = frozenset()) -> Any:
    """Recursively redact model-originated PII in EVERY string a response carries.

    Shape-following walkers are how response redaction gets quietly bypassed. A walker
    that visits `message.content`, tool-call arguments and Anthropic `text` blocks looks
    complete against today's schemas and silently misses list-valued content parts,
    `refusal`, `reasoning_content`, and Anthropic `thinking` / `tool_use` inputs -- every
    one of them model-generated text that reaches the client. Each new provider field is
    then a leak until someone remembers to add it here.

    So the default is to scan, and the exception list is explicit: `_SSE_STRUCTURAL_KEYS`
    values are left alone because rewriting them changes what the response MEANS rather
    than what it discloses (an id, a model name, a finish_reason is not PII, and mangling
    them breaks clients for no privacy gain). This is the non-streaming counterpart of
    `_redact_sibling_strings`, which applies the same rule per SSE event.

    `skip_keys` exists for the streaming caller, whose ordered content channel is already
    handled by the retention buffer. The non-streaming caller passes nothing: there is no
    buffer, so content must be scanned here or not at all.
    """
    if isinstance(node, dict):
        return {
            key: (
                value
                if key in _SSE_STRUCTURAL_KEYS or key in skip_keys
                else redact_model_originated_tree(value, vault, skip_keys)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [redact_model_originated_tree(item, vault, skip_keys) for item in node]
    if isinstance(node, str):
        return redact_model_originated_text(node, vault)
    return node


def _redact_across_join(tail: str, fragment: str, vault: Any) -> str:
    """Redact the part of a value that this fragment COMPLETES across an event boundary.

    `tail` is text already emitted on this JSON path, `fragment` is the text about to be
    emitted on it. Neither matched a detector alone -- that is why the leak exists -- but
    their concatenation does, and a client that appends the field reconstructs the value.

    The probe is a scratch view and is never emitted. Only spans that STRADDLE the join
    are acted on: a span lying wholly in `tail` was already emitted and cannot be recalled,
    and a span lying wholly in `fragment` is the caller's per-event scan's job and has
    already been handled by the time this runs. Redacting the completing half is enough,
    because the prefix was emitted precisely because nothing matched it; what the client
    keeps is a partial identifier, not a recoverable value. That residue is a documented
    reduction in disclosure, not an elimination of it.

    Both halves of the probe are clipped to the retention window, the same bound the
    ordered content channel holds back at its own emit boundary. A straddling span longer
    than that window is out of scope for the content channel too, and clipping is what
    keeps this constant-cost per path per event instead of proportional to field length.
    """
    if not tail or not fragment:
        return fragment
    from llm_shield_proxy.engines.pii_engine import pii_engine

    window = settings.RESPONSE_PII_SCAN_WINDOW
    head = tail[-window:] if len(tail) > window else tail
    probe = head + (fragment[:window] if len(fragment) > window else fragment)
    join = len(head)

    try:
        spans = pii_engine.detect_spans(probe)
    except Exception:  # noqa: BLE001
        # Same fail-open posture as `redact_model_originated_text`, and for the same
        # reason: a scanner error must not blank the client's response. Logged, not silent.
        logger.warning("Cross-event sibling scan failed; forwarding fragment unscanned", exc_info=True)
        return fragment

    known = getattr(vault, "token_to_original", None) or {}
    # Every straddling span starts before the join, so they all cover the same prefix of
    # `fragment`; the one reaching furthest subsumes the rest and one slice replaces them
    # all. No per-character list is built on this path.
    furthest_end = -1
    entity = None
    for start, end, entity_type, matched_text in spans:
        if start >= join or end <= join or matched_text in known:
            continue
        if end > furthest_end:
            furthest_end = end
            entity = entity_type
    if entity is None:
        return fragment
    return f"[{entity}_REDACTED]{fragment[furthest_end - join:]}"


class _SiblingPathTails:
    """Bounded, per-stream memory of what each sibling JSON path already emitted.

    Keyed by JSON PATH rather than by key name, because that is the only join a client
    actually performs: two events agreeing on `choices[0].delta.note` are the same field,
    while `note` under two different parents are two different fields and joining them
    would invent a value nobody ever sees.

    Bounded because the keys are upstream-chosen. Insertion order IS recency order here --
    `record` pops before it sets -- so the oldest key is the least recently seen and
    eviction is `next(iter(...))`. Evictions are counted and surfaced, never silent.
    """

    __slots__ = ("_tails", "_max_paths", "evictions")

    def __init__(self, max_paths: Optional[int] = None) -> None:
        self._tails: Dict[str, str] = {}
        self._max_paths = MAX_SIBLING_PATHS if max_paths is None else max_paths
        self.evictions = 0

    def __len__(self) -> int:
        return len(self._tails)

    @property
    def tracked_paths(self) -> tuple:
        """The paths currently held, oldest-seen first. For tests and diagnostics."""
        return tuple(self._tails)

    def scan(self, path: str, fragment: str, vault: Any) -> str:
        """Redacts what `fragment` completes on `path`, then remembers what it emits.

        One `pop` and one assignment per path per event, and one slice to build the tail.
        An empty fragment is not an emission and must not erase the tail: a provider that
        sends `""` on a field between two halves of a value would otherwise clear the very
        memory this exists for.

        The tail ACCUMULATES and is then clipped, rather than being replaced by the latest
        fragment. A value split three ways puts nothing usable in any single fragment, so a
        tail that remembered only the last one would never see the first, and the event
        that completes the value would probe a join that does not exist.
        """
        if not fragment:
            return fragment
        tails = self._tails
        # pop, not get: re-inserting below is what moves this path to the recency end.
        previous = tails.pop(path, "")
        emitted = _redact_across_join(previous, fragment, vault)
        window = settings.RESPONSE_PII_SCAN_WINDOW
        joined = previous + emitted
        tails[path] = joined[-window:] if len(joined) > window else joined
        if len(tails) > self._max_paths:
            self._evict_oldest()
        return emitted

    def _evict_oldest(self) -> None:
        tails = self._tails
        while len(tails) > self._max_paths:
            del tails[next(iter(tails))]
            self.evictions += 1
        logger.warning(
            "SSE stream exceeded %d tracked sibling JSON paths; evicted least-recently-seen "
            "(cross-event split detection is reduced on evicted paths)",
            self._max_paths,
        )
        try:
            from llm_shield_proxy.observability.metrics import (
                llm_shield_sse_sibling_paths_evicted_total,
            )

            llm_shield_sse_sibling_paths_evicted_total.inc()
        except Exception:  # noqa: BLE001
            # Metrics must never be able to break a response stream.
            logger.debug("Could not record a sibling-path eviction", exc_info=True)


def _redact_sibling_strings(
    node: Any,
    buffer: "SSERehydrationBuffer",
    skip_content: bool = True,
    tails: Optional[_SiblingPathTails] = None,
    path: str = "",
) -> Any:
    """Redact model-originated PII in event fields OTHER than the delta content.

    The content field is the ordered stream and is handled by the retention buffer, which
    reassembles values split across events. Everything else in the event JSON was
    previously forwarded untouched, so a value carried in a sibling field reached the
    client unscanned however obvious it was. Measured at LeakRate 1.00 for that carrier
    by the v2 conformance profile, against LeakRate 0.00 for delta content.

    This is the response-path counterpart of the deep request walk added in 1.5.1: the
    request body is walked recursively, and until now the response was not.

    `tails` closes the cross-event half of that gap. Sibling fields are still never
    buffered or delayed -- an arbitrary JSON field has no defined concatenation semantics,
    and withholding the tail of a field a client may treat as "last value wins" would
    delay or corrupt a value rather than protect one. Instead each path remembers a bounded
    tail of what it ALREADY emitted, and a fragment that completes a value across that
    boundary is redacted on its way out. See `_redact_across_join`.

    REMAINING LIMIT, still declared rather than hidden: the already-emitted prefix stays
    with the client. It is a partial identifier and not a recoverable value, but it is
    residue, and the fix is a reduction in disclosure rather than an elimination of it.
    Passing no `tails` keeps the older chunk-local behaviour, which is what the
    non-streaming callers want: they have no stream to join across.
    """
    if isinstance(node, dict):
        return {
            key: (
                value
                if key in _SSE_STRUCTURAL_KEYS or (skip_content and key in _BUFFERED_CHANNEL_KEYS)
                # Structural keys are skipped BEFORE the path is extended, so an `id` or a
                # `model` never becomes a tracked path. They are stable across events, so
                # probing them would join a value to itself and match on every event.
                else _redact_sibling_strings(
                    value, buffer, skip_content, tails, f"{path}.{key}"
                )
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        # Keyed by the entry's own `index`, not its position. `choices[0]` can be index 0
        # in one event and index 1 in the next, and keying by position would then join
        # text from two different answers.
        return [
            _redact_sibling_strings(
                item,
                buffer,
                skip_content,
                tails,
                f"{path}[{_list_entry_identity(item, position)}]",
            )
            for position, item in enumerate(node)
        ]
    if isinstance(node, str):
        # Chunk-local first: a value whole inside this fragment is redacted here, and the
        # cross-boundary probe then runs over the text as it will actually be EMITTED,
        # which is also what the path's tail must remember.
        emitted = buffer._redact_model_originated(node)
        if tails is not None:
            emitted = tails.scan(path, emitted, buffer.vault)
        return emitted
    return node


class _BoundedOutputCoalescer:
    """Aggregate small output pieces without making write boundaries a memory risk."""

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
    """Sliding-window buffer preventing partial entity token leakage across SSE stream chunks."""

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
        """Characters to hold back so a detectable value cannot straddle the boundary."""
        if not text:
            return 0
        window = min(len(text), settings.RESPONSE_PII_SCAN_WINDOW)
        tail = text[len(text) - window:]
        boundary = tail.rfind(" ")
        return window - boundary - 1 if boundary != -1 else window

    def _redact_model_originated(self, text: str) -> str:
        """Delegate to the module-level scanner so the non-streaming path can reuse it."""
        return redact_model_originated_text(text, self.vault)

    def _calculate_retention_length(self, text: str) -> int:
        """Calculates the minimum trailing retention boundary needed for text."""
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
        """Processes incoming delta text chunk and emits safe, rehydrated content."""
        # No span here. This runs once per SSE delta; a span here would emit one
        # span per token to any collector. rehydrate_sse_stream opens one span for
        # the whole stream.
        emitted_parts = []

        if delta_text:
            self.content_buffer += delta_text

            # Enforce maximum safety length on the buffer
            if len(self.content_buffer) > 64 * 1024:
                raise StreamCapacityExceeded("SSE buffer exceeded maximum safety threshold (backpressure protection)")

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
    """Asynchronous generator consuming raw SSE bytes and yielding rehydrated SSE chunks."""
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
        # One retention window PER CHANNEL, not one per stream.
        buffers: Dict[_WindowKey, SSERehydrationBuffer] = {}
        # Per-path memory for the sibling scan.
        sibling_tails = _SiblingPathTails()
        # One escaping view for the whole stream.
        json_vault: Optional[_JsonStringVaultView] = None

        def _channel_vault(key: _WindowKey) -> Any:
            """The vault a channel restores against: escaped for JSON, raw for prose."""
            nonlocal json_vault
            if key[1] is None:
                return vault
            if json_vault is None:
                json_vault = _JsonStringVaultView(vault)
            return json_vault

        def _buffer_for(key: _WindowKey) -> SSERehydrationBuffer:
            """The retention window for one channel, created on first sight."""
            existing = buffers.get(key)
            if existing is not None:
                return existing
            if len(buffers) >= MAX_STREAM_WINDOWS:
                # Fail closed. Reusing another channel's window here would reintroduce
                # exactly the splicing this keying exists to prevent.
                raise StreamCapacityExceeded(
                    f"SSE stream opened more than {MAX_STREAM_WINDOWS} retention windows"
                )
            created = SSERehydrationBuffer(
                _channel_vault(key), max_output_bytes=max_output_piece_bytes
            )
            buffers[key] = created
            return created

        def _flush_window(key: _WindowKey) -> str:
            """Drains one window, or returns empty if it was never opened."""
            window = buffers.get(key)
            return window.process_delta_text("", is_final=True) if window is not None else ""

        def _tool_windows_of(choice_index: int) -> tuple:
            """This choice's tool-call windows, in tool-call index order."""
            return tuple(sorted(k for k in buffers if k[0] == choice_index and k[1] is not None))

        def _flush_tool_windows_into(
            delta: dict, choice_index: int
        ) -> Optional[SSERehydrationBuffer]:
            """Empties this choice's tool-call windows INTO the event that finishes it."""
            flushed = None
            for key in _tool_windows_of(choice_index):
                remaining = _flush_window(key)
                if remaining:
                    _append_tool_arguments(delta, key[1], remaining)
                    flushed = buffers[key]
            return flushed

        def _flush_all_windows() -> Iterator[bytes]:
            """Drains every window still open, one event per channel, tagged with its keys.

            Driven by the windows rather than by the last event seen: a choice that
            finished earlier is not named in the terminal event, and flushing only what
            that event carries would drop its held text and truncate its answer. Tool-call
            windows are normally already empty here, having been flushed into their
            finishing event; this is the net for a stream that stopped without one.
            """
            # `None` sorts before any int so a choice's content precedes its tool calls.
            for key in sorted(buffers, key=lambda k: (k[0], -1 if k[1] is None else k[1])):
                remaining = _flush_window(key)
                if not remaining:
                    continue
                choice_index, tool_index = key
                if tool_index is None:
                    delta: dict = {"content": remaining}
                else:
                    delta = {}
                    _append_tool_arguments(delta, tool_index, remaining)
                flush_obj = {"choices": [{"index": choice_index, "delta": delta}]}
                yield f"data: {json.dumps(flush_obj).decode('utf-8')}\n\n".encode()

        # Anthropic block index -> the OpenAI tool-call ordinal it translates to. Anthropic
        # numbers tool blocks in the same space as text blocks, so a reply whose first block
        # is prose would otherwise open a tool call at index 1 with no index 0 in front of
        # it. Clients concatenate by index, so ordinals are handed out densely from 0.
        anthropic_tool_ordinals: Dict[int, int] = {}

        def _anthropic_tool_ordinal(block_index: int) -> int:
            """The tool-call ordinal for one Anthropic content block, stable per stream.

            Past MAX_ANTHROPIC_TOOL_ORDINALS this RAISES, which the chunk handler turns
            into a fail-closed abort with an error event and a terminator. That is
            exactly what `_buffer_for` already does past MAX_STREAM_WINDOWS, and the two
            caps guard the same class of upstream: one inventing indices without limit.

            Two rejected alternatives, both of which traded this bug for a worse one:

            Recycling the last ordinal kept the stream well-formed but that ordinal
            already belongs to a real block, so every over-cap block shared its
            retention window and its client-visible tool-call index -- two distinct tool
            calls silently merged into one.

            Leaving the block untranslated avoided the merge but emitted a native
            Anthropic event into an OpenAI-shaped stream, which no client on that
            contract can parse, and dropped the block onto the chunk-local sweep, which
            has no cross-event retention -- so a value split across two `partial_json`
            fragments would not be caught.

            Refusing the stream is the only one of the three that keeps the output
            contract, the cross-event guarantee and the memory bound at once.
            """
            existing = anthropic_tool_ordinals.get(block_index)
            if existing is not None:
                return existing
            if len(anthropic_tool_ordinals) >= MAX_ANTHROPIC_TOOL_ORDINALS:
                raise StreamCapacityExceeded(
                    f"Anthropic stream opened more than {MAX_ANTHROPIC_TOOL_ORDINALS} "
                    "tool-block indices"
                )
            assigned = len(anthropic_tool_ordinals)
            anthropic_tool_ordinals[block_index] = assigned
            return assigned

        def _tool_call_line(ordinal: int, arguments: str, opener: Optional[dict] = None) -> str:
            """One OpenAI-shaped SSE line carrying a tool-call fragment.

            `opener` is the Anthropic `content_block` that started the call, and supplies
            the `id` and `name` an OpenAI client needs once, on the call's first event.
            """
            function: Dict[str, Any] = {"arguments": arguments}
            call: Dict[str, Any] = {"index": ordinal, "function": function}
            if opener is not None:
                call["id"] = opener.get("id")
                call["type"] = "function"
                function["name"] = opener.get("name")
            chunk = {
                "id": cached_id,
                "object": "chat.completion.chunk",
                "created": cached_created,
                "model": cached_model,
                "choices": [{"index": 0, "delta": {"tool_calls": [call]}}],
            }
            return f"data: {json.dumps(chunk).decode('utf-8')}"

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def _bounded_output(piece: bytes) -> bytes:
            if len(piece) > max_output_piece_bytes:
                raise StreamCapacityExceeded("Rehydrated SSE output exceeded maximum safe length")
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

                # Lines produced from THIS upstream chunk, emitted in bounded writes.
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
                            # Abort means abort. Keeps the `finally` block from flushing.
                            stream_aborted = True
                            break
                        if chunk_text:
                            canary_tail = (canary_tail + chunk_text)[-(canary_len - 1):] if canary_len > 1 else ""

                    line_accumulator += chunk_text

                    if len(line_accumulator) > max_line_length:
                        raise StreamCapacityExceeded("Line accumulator exceeded maximum safe length (Slowloris protection)")

                    while "\n" in line_accumulator:
                        line, line_accumulator = line_accumulator.split("\n", 1)
                        stripped = line.strip()
                        # Lines this event must emit BEFORE itself.
                        pre_lines: list = []

                        if stripped.startswith("event: "):
                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready
                            continue

                        # The space after `data:` is optional in the SSE spec.
                        sse_payload = stripped[5:].lstrip() if stripped.startswith("data:") else None
                        if sse_payload is not None and sse_payload != "[DONE]":
                            try:
                                # True once a branch has routed this event's content into a retention window.
                                scanned_line = False
                                json_str = sse_payload
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
                                    # Every choice the event carries, not just the first.
                                    sibling_buffer = None
                                    for choice in choices:
                                        if not isinstance(choice, dict):
                                            continue
                                        delta = choice.get("delta")
                                        if not isinstance(delta, dict):
                                            continue
                                        choice_index = _entry_index(choice)
                                        if isinstance(delta.get("content"), str):
                                            scanned_line = True
                                            content_window = _buffer_for((choice_index, None))
                                            delta["content"] = content_window.process_delta_text(delta["content"])
                                            sibling_buffer = content_window
                                        # Tool arguments are model-generated text on their own ordered channel.
                                        for tool_index, function in _tool_argument_fragments(delta):
                                            scanned_line = True
                                            tool_window = _buffer_for((choice_index, tool_index))
                                            function["arguments"] = tool_window.process_delta_text(
                                                function["arguments"]
                                            )
                                            sibling_buffer = tool_window
                                        # A finished choice parses its arguments now.
                                        if choice.get("finish_reason") is not None:
                                            drained = _flush_tool_windows_into(delta, choice_index)
                                            # Borrow the drained window rather than opening one.
                                            sibling_buffer = sibling_buffer or drained
                                        choice["delta"] = delta
                                    if sibling_buffer is None:
                                        # An event can carry only sibling fields.
                                        sibling_buffer = next(iter(buffers.values()), None)
                                        if sibling_buffer is None:
                                            try:
                                                sibling_buffer = _buffer_for((0, None))
                                            except ValueError:
                                                logger.warning(
                                                    "SSE window ceiling reached; sibling "
                                                    "fields in this event were not scanned"
                                                )
                                    if sibling_buffer is not None:
                                        if settings.ENABLE_RESPONSE_PII_REDACTION:
                                            # Sibling fields of the event.
                                            data_obj = _redact_sibling_strings(
                                                data_obj, sibling_buffer, tails=sibling_tails
                                            )
                                        line = f"data: {json.dumps(data_obj).decode('utf-8')}"
                                # 2. Anthropic Content Block Delta
                                elif "delta" in data_obj and isinstance(data_obj["delta"], dict):
                                    delta = data_obj["delta"]
                                    if "text" in delta and isinstance(delta["text"], str):
                                        scanned_line = True
                                        raw_content = delta["text"]
                                        # One window for the whole Anthropic stream.
                                        rehydrated_content = _buffer_for((0, None)).process_delta_text(raw_content)
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
                                    elif isinstance(delta.get("partial_json"), str):
                                        # Anthropic streams tool input as `partial_json` fragments.
                                        scanned_line = True
                                        tool_ordinal = _anthropic_tool_ordinal(_entry_index(data_obj))
                                        restored = _buffer_for((0, tool_ordinal)).process_delta_text(
                                            delta["partial_json"]
                                        )
                                        line = _tool_call_line(tool_ordinal, restored)
                                    else:
                                        pass  # Skip deltas carrying neither text nor tool input
                                # 3. Anthropic Content Block Start / Generic text delta
                                elif "content_block" in data_obj and isinstance(data_obj["content_block"], dict):
                                    cb = data_obj["content_block"]
                                    if "text" in cb and isinstance(cb["text"], str):
                                        scanned_line = True
                                        raw_content = cb["text"]
                                        # Same single window as the delta branch above.
                                        rehydrated_content = _buffer_for((0, None)).process_delta_text(raw_content)
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
                                    elif cb.get("type") == "tool_use":
                                        # Opens the call so the client learns its id and name once.
                                        scanned_line = True
                                        tool_ordinal = _anthropic_tool_ordinal(_entry_index(data_obj))
                                        line = _tool_call_line(tool_ordinal, "", opener=cb)
                                    else:
                                        pass  # Skip start blocks carrying neither
                                elif data_obj.get("type") == "content_block_stop":
                                    # This block's arguments are complete here.
                                    stopped = anthropic_tool_ordinals.get(_entry_index(data_obj))
                                    if stopped is not None:
                                        stopped_tail = _flush_window((0, stopped))
                                        if stopped_tail:
                                            pre_lines.append(_tool_call_line(stopped, stopped_tail))
                                elif data_obj.get("type") in ("message_stop", "message_delta", "ping"):
                                    pass  # We let [DONE] be handled at stream end

                                # Scan by default. The branches above route the shapes we know.
                                if (
                                    not scanned_line
                                    and settings.ENABLE_RESPONSE_PII_REDACTION
                                ):
                                    # This scan is vault-scoped, so ANY open window answers.
                                    sweep_buffer = next(iter(buffers.values()), None)
                                    if sweep_buffer is None:
                                        try:
                                            sweep_buffer = _buffer_for((0, None))
                                        except ValueError:
                                            logger.warning(
                                                "SSE window ceiling reached; this event was "
                                                "not scanned"
                                            )
                                    if sweep_buffer is not None:
                                        swept = _redact_sibling_strings(
                                            data_obj, sweep_buffer, skip_content=False
                                        )
                                        if swept != data_obj:
                                            line = f"data: {json.dumps(swept).decode('utf-8')}"
                            except (json.JSONDecodeError, TypeError, KeyError):
                                pass

                            for pre_line in pre_lines:
                                # Terminated here, with its OWN blank line.
                                for ready in _queue_output((pre_line + "\n\n").encode("utf-8")):
                                    yield ready
                            for ready in _queue_output((line + "\n").encode("utf-8")):
                                yield ready
                        elif stripped == "data: [DONE]":
                            # Flush every choice's window BEFORE yielding the [DONE] signal
                            for flush_piece in _flush_all_windows():
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
                        # Already rehydrated and safe.
                        for ready in buffered:
                            yield ready

                    # A deliberate safety limit is never forgiven by FAIL_OPEN.
                    capacity_breach = isinstance(e, StreamCapacityExceeded)
                    if settings.SHIELD_FAILURE_MODE == "FAIL_CLOSED" or capacity_breach:
                        # Type name only.
                        logging.getLogger(__name__).error(
                            "Streaming rehydration failed (%s): %s",
                            "capacity" if capacity_breach else "FAIL_CLOSED",
                            type(e).__name__,
                        )
                        stream_aborted = True
                        # Fail closed, but not silently.
                        error_event = {
                            "error": {
                                "message": (
                                    "The response stream was stopped by LLM-Shield-Proxy "
                                    "because it could not be safely processed."
                                ),
                                "type": "shield_stream_aborted",
                                "code": "stream_aborted",
                            }
                        }
                        yield f"data: {json.dumps(error_event).decode('utf-8')}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return
                    else:
                        logging.getLogger(__name__).error(
                            "Streaming rehydration failed (FAIL_OPEN): %s", type(e).__name__
                        )
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

                for flush_piece in _flush_all_windows():
                    yield flush_piece

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
                    # An upstream that stops mid-line leaves a fragment here.
                    logger.warning(
                        "Upstream stream ended mid-line; dropped %d unterminated bytes "
                        "rather than forwarding them unscanned",
                        len(line_accumulator.encode("utf-8")),
                    )
                    line_accumulator = ""

    # One span for the whole stream, replacing one span per delta.
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
