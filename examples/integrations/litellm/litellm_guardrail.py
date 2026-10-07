# +-------------------------------------------------------------+
#
#         Use LLM Shield Proxy for reversible PII redaction
#            https://github.com/ninadphalak/LLM-Shield-Proxy
#
# +-------------------------------------------------------------+
"""LiteLLM guardrail adapter: reversible PII redaction and restoration.

This is the out-of-tree form of the ``llm_shield_proxy`` guardrail. It is an example
artifact rather than a module of the published package, because it imports LiteLLM at
module scope and this distribution must not declare a dependency it cannot satisfy:
``tests/ootb/_import_every_module.py`` imports every packaged module on a wheel-only
install and fails on exactly that. It also shares no file with LiteLLM's own tree,
which is what made the in-tree submission conflict on every upstream guardrail.

Put the file where the LiteLLM proxy can import it -- LiteLLM documents this for
custom guardrails -- so it lands as ``/app/litellm_guardrail.py`` in the container:

    -v $(pwd)/litellm_guardrail.py:/app/litellm_guardrail.py

Then point a LiteLLM ``config.yaml`` at the class by dotted path::

    guardrails:
      - guardrail_name: "llm-shield"
        litellm_params:
          guardrail: litellm_guardrail.LLMShieldProxyGuardrail
          mode: ["pre_call", "post_call"]
          api_base: http://localhost:8000
          api_key: os.environ/LLM_SHIELD_PROXY_API_KEY

Both modes belong on one entry: ``pre_call`` redacts the outbound request and
``post_call`` restores the reply. ``pre_call`` alone redacts the request and
hands the placeholders straight back to the caller.

LiteLLM is a host, not a dependency. This module imports LiteLLM; the package
never imports this module, so a bare ``pip install llm-shield-proxy`` stays free
of LiteLLM's dependency tree. ``llm-shield-proxy[litellm]`` declares the host
for the case where it is installed into the same environment.

``from __future__ import annotations`` is load-bearing: the signature unions
below are written as ``str | None``, which is a runtime TypeError on the 3.9
interpreter this package still supports.
"""

from __future__ import annotations

import copy
import functools
import itertools
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from enum import Enum
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,  # noqa: TID251  # **kwargs forwards verbatim to CustomGuardrail.__init__
    ClassVar,
    Final,
    Literal,
    Optional,
    TypeAlias,
)

import httpx
from litellm._logging import verbose_proxy_logger
from litellm.exceptions import GuardrailRaisedException
from litellm.integrations.custom_guardrail import (
    CustomGuardrail,
    log_guardrail_information,
)
from litellm.llms.custom_httpx.http_handler import (
    get_async_httpx_client,
    httpxSpecialProvider,
)
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.guardrails import GuardrailEventHooks
from litellm.types.utils import GenericGuardrailAPIInputs

if TYPE_CHECKING:
    from litellm.caching.caching import DualCache
    from litellm.litellm_core_utils.litellm_logging import Logging as LiteLLMLoggingObj

GUARDRAIL_NAME: Final = "llm_shield_proxy"

_DEFAULT_API_BASE: Final = "http://localhost:8000"
_REDACT_PATH: Final = "/v1/guard/redact"
_REHYDRATE_PATH: Final = "/v1/guard/rehydrate"
_REHYDRATE_STREAM_PATH: Final = "/v1/guard/rehydrate/stream"

# The session id ties a redact call to the rehydrate calls that undo it. It is
# stored on the request dict rather than on the guardrail instance: the proxy
# registers one instance process-wide, so instance attributes would be shared
# across concurrent requests.
_SESSION_METADATA_KEY: Final = "llm_shield_session_id"

# Roles whose text the application author wrote and the caller never sees. Their
# PII is still redacted outbound, but it is not restorable from the reply.
_PRIVILEGED_ROLES: Final = frozenset({"system", "developer"})

# Vault ids are minted here and never derived from anything the caller sends. The
# vault holds the plaintext behind every placeholder, so an id a caller could
# supply or guess would let one user rehydrate another user's values by getting a
# placeholder echoed back. The per-process prefix means a caller cannot even name
# a vault this process uses.
_VAULT_PREFIX: Final = f"litellm-{uuid.uuid4().hex}"

_DEFAULT_TIMEOUT_SECONDS: Final = 10.0

# The proxy's own request dict. Mutable by design: a pre-call guardrail rewrites
# the caller's payload in place, which is the entire point of the hook.
# mutable-ok: the shape is fixed by CustomLogger's hook signatures.
MutableRequest: TypeAlias = dict

# A JSON body on its way to httpx, which requires a real dict rather than a view.
# mutable-ok: handed straight to the HTTP client.
JsonBody: TypeAlias = dict

# One redactable span: the text as it stands, and the write that puts the
# replacement back where it came from.
# How far a tool_result chain is followed. Real payloads nest one or two deep; the
# bound is what stops a crafted one from becoming an unbounded walk.
_MAX_CONTENT_DEPTH: Final = 8

_Slot: TypeAlias = tuple[str, Callable[[str], None]]  # mutable-ok: Callable's param list.

# The accumulator the collectors below append into. It never escapes
# _locate_request_texts, which freezes it into a tuple before returning.
_SlotSink: TypeAlias = list[_Slot]  # mutable-ok: accumulator passed between collectors.

# Sliding windows keyed by (choice index, tool-call index | None), threaded through one
# stream. `None` is the content channel; an int is one tool call's accumulating
# `arguments`. Content and each tool call are separate token streams, so each needs its
# own window -- one shared window would splice one stream's held-back tail onto another.
_CarryWindows: TypeAlias = dict  # mutable-ok: per-stream windows advanced in place.

# A caller-owned list whose entries are rewritten in place, such as a Completions
# `prompt` sent as an array of strings.
MutableSeq: TypeAlias = list  # mutable-ok: the request payload's own list.


def _collect(container: MutableRequest, key: str, slots: _SlotSink) -> None:
    """Records the string at `key`, along with the write that replaces it."""
    value: Final = container.get(key)
    if isinstance(value, str) and value:
        slots.append((value, lambda new, c=container, k=key: c.__setitem__(k, new)))


def _collect_entry(entries: MutableSeq, index: int, slots: _SlotSink) -> None:
    """Records a string held directly in a list, rather than under a key."""
    value: Final = entries[index]
    if isinstance(value, str) and value:
        slots.append((value, lambda new, e=entries, i=index: e.__setitem__(i, new)))


def _collect_prompt(data: MutableRequest, slots: _SlotSink) -> None:
    """The Completions API sends its text in `prompt`, and its tail in `suffix`."""
    _collect(data, "suffix", slots)
    prompt: Final = data.get("prompt")
    if isinstance(prompt, str):
        _collect(data, "prompt", slots)
        return
    if isinstance(prompt, dict):
        # A Responses API PromptObject. `variables` are substituted into the stored
        # prompt on the provider side, so they are caller text. `id` and `version`
        # identify which prompt to use and must arrive unchanged.
        variables: Final = prompt.get("variables")
        if isinstance(variables, dict):
            for name in tuple(variables):
                _collect(variables, name, slots)
        return
    if not isinstance(prompt, list):
        return
    for index in range(len(prompt)):
        _collect_entry(prompt, index, slots)


def _collect_content(container: MutableRequest, slots: _SlotSink) -> None:
    """Collects `content`, a string or a list of typed parts.

    An Anthropic tool_result nests its own content, so this has to descend. It walks
    with an explicit stack and a depth bound rather than by recursion: the nesting is
    caller controlled, and an unbounded descent is a JSON bomb.
    """
    # Walked in document order: the shield maps its replies back by position, so the
    # order spans are collected in is part of the contract.
    pending: Final[list] = [(container, 0)]  # mutable-ok: local queue, never escapes.
    cursor = 0  # rebind-ok: advances through the queue.
    while cursor < len(pending):
        node, depth = pending[cursor]
        cursor += 1
        content = node.get("content")
        if isinstance(content, str):
            _collect(node, "content", slots)
            continue
        if depth >= _MAX_CONTENT_DEPTH:
            continue
        for part in content if isinstance(content, list) else ():
            if not isinstance(part, dict):
                continue
            # Image and audio parts have no text and fall through untouched.
            _collect(part, "text", slots)
            if "content" in part:
                pending.append((part, depth + 1))


def _collect_participant_name(message: MutableRequest, slots: _SlotSink) -> None:
    """Redacts `name` where it identifies a person, never where it names a function.

    On a user or assistant turn `name` is the participant, which is personal data.
    On a tool or function turn the same field carries the function's name and has
    to reach the provider unchanged, or the call no longer routes.
    """
    if message.get("role") in ("tool", "function"):
        return
    _collect(message, "name", slots)


def _collect_tool_arguments(message: MutableRequest, slots: _SlotSink) -> None:
    """Tool arguments carry the values a user asked the model to act on."""
    for tool_call in message.get("tool_calls") or ():
        function = tool_call.get("function") if isinstance(tool_call, dict) else None  # rebind-ok: loop variable.
        if isinstance(function, dict):
            _collect(function, "arguments", slots)
    legacy: Final = message.get("function_call")
    if isinstance(legacy, dict):
        _collect(legacy, "arguments", slots)


def _collect_system(data: MutableRequest, slots: _SlotSink) -> None:
    """Anthropic's /v1/messages carries its system prompt at the top level."""
    system: Final = data.get("system")
    if isinstance(system, str):
        _collect(data, "system", slots)
        return
    for part in system if isinstance(system, list) else ():
        if isinstance(part, dict):
            _collect(part, "text", slots)


def _collect_responses_fields(data: MutableRequest, slots: _SlotSink, privileged: _SlotSink) -> None:
    """The Responses API sends text outside `messages`, in `instructions` and `input`.

    `instructions` is written by the application, not by the caller, so it is
    collected into the privileged sink; `input` is the caller's own text.
    """
    _collect(data, "instructions", privileged)
    request_input: Final = data.get("input")
    if isinstance(request_input, str):
        _collect(data, "input", slots)
        return
    if not isinstance(request_input, list):
        return
    for index, item in enumerate(request_input):
        if isinstance(item, str):
            # The embeddings and moderations shape: `input` as an array of strings.
            _collect_entry(request_input, index, slots)
            continue
        if not isinstance(item, dict):
            continue
        _collect_content(item, slots)
        # A function_call item holds `arguments`; a function_call_output holds `output`.
        _collect(item, "arguments", slots)
        _collect(item, "output", slots)


def _choice_index(choice: object) -> int:
    """Streaming choices are matched across chunks by their index."""
    index: Final = getattr(choice, "index", 0)
    return index if isinstance(index, int) else 0


def _read_field(holder: object, name: str) -> object:
    """Reads one field from a dict or from an object.

    LiteLLM's replies arrive as Pydantic models on some paths and as plain dicts on
    others, depending how far they have been deserialised, so every response walk here
    has to handle both shapes.
    """
    if isinstance(holder, dict):
        return holder.get(name)
    return getattr(holder, name, None)


def _write_field(holder: object, name: str, value: str) -> None:
    """Writes one string field back into a dict or an object. Pairs with _read_field."""
    if isinstance(holder, dict):
        holder[name] = value
    else:
        setattr(holder, name, value)


def _collect_json_leaves(node: object, slots: _SlotSink, depth: int = 0) -> None:
    """Collects every string leaf of a JSON-ish structure, with a write-back per leaf.

    An Anthropic `tool_use` block carries `input`, an arbitrary JSON object rather than a
    string, so a value worth restoring can sit at any depth. Bounded by
    `_MAX_CONTENT_DEPTH` for the same reason the request walk is: the shape is model
    controlled, and the bound is what stops a crafted one from becoming an unbounded
    descent.
    """
    if depth > _MAX_CONTENT_DEPTH:
        return
    if isinstance(node, dict):
        for key in tuple(node):
            value = node[key]
            if isinstance(value, str) and value:
                slots.append((value, lambda new, d=node, k=key: d.__setitem__(k, new)))
            else:
                _collect_json_leaves(value, slots, depth + 1)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and value:
                slots.append((value, lambda new, entries=node, i=index: entries.__setitem__(i, new)))
            else:
                _collect_json_leaves(value, slots, depth + 1)


def _carry_sort_key(key: tuple) -> tuple:
    """Orders streaming windows without ever comparing None to an int.

    `sorted()` over the raw keys raises as soon as one choice holds both a content window
    and a tool-call window, because `None < 0` is not orderable. Content sorts first, then
    tool calls by their index.
    """
    choice_index, tool_index = key
    return (choice_index, -1 if tool_index is None else tool_index)


def _read_list(holder: object, name: str) -> Sequence[object]:
    """Reads a list field from a dict or an object; anything else reads as empty.

    The entries are the reply's own objects, so writing through them edits the reply.
    """
    value: Final = _read_field(holder, name)
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _collect_response_item(item: object, slots: _SlotSink) -> None:
    """Restorable spans in one Responses API output item, dict or object.

    Mirrors `_collect_responses_fields` on the request side -- a function_call or
    mcp_call item holds `arguments`, their outputs `output`, a reasoning item `summary`
    parts -- so the two directions stay symmetric. A custom tool call carries `input` and
    a code interpreter call `code`, both model-written.
    """
    for block in _read_list(item, "content"):
        for field in ("text", "refusal"):
            text = _read_field(block, field)
            if isinstance(text, str) and text:
                slots.append((text, lambda new, b=block, f=field: _write_field(b, f, new)))
    for part in _read_list(item, "summary"):
        text = _read_field(part, "text")
        if isinstance(text, str) and text:
            slots.append((text, lambda new, p=part: _write_field(p, "text", new)))
    for field in ("arguments", "output", "input", "code"):
        value = _read_field(item, field)
        if isinstance(value, str) and value:
            slots.append((value, lambda new, i=item, f=field: _write_field(i, f, new)))


# The two native stream shapes have no `choices`. Anthropic `/v1/messages` reaches the
# streaming hook as raw SSE text, one network chunk at a time; the Responses API reaches it
# as typed events. Each gets a restorer with the same per-stream windows the chat path uses.

_StreamStep: TypeAlias = Callable[[str, str, bool], Awaitable[tuple[str, str]]]  # mutable-ok: Callable's param list.
_Rehydrate: TypeAlias = Callable[[Sequence[str]], Awaitable[Sequence[str]]]  # mutable-ok: Callable's param list.

_ANTHROPIC_DELTA_FIELDS: Final = MappingProxyType({"text_delta": "text", "input_json_delta": "partial_json"})

_SSE_EVENT_BOUNDARY: Final = re.compile(rb"(\r?\n\r?\n)")

_SSE_OPENINGS: Final = (b"event:", b"data:", b"id:", b"retry:", b":")

_RESPONSES_BINARY_DELTAS: Final = frozenset(("response.audio.delta",))

_RESPONSES_STRUCTURAL_FIELDS: Final = frozenset(
    ("type", "id", "item_id", "call_id", "name", "server_label", "status", "obfuscation")
)

_RESPONSES_TERMINAL_EVENTS: Final = frozenset(("response.completed", "response.incomplete"))

# An SSE event is held until its blank-line terminator arrives. The core package caps its own
# line accumulator at 1 MiB (`MAX_SSE_LINE_LENGTH`) and fails closed past it; an upstream that
# never sends a terminator must not grow this buffer without bound either.
_MAX_SSE_PENDING_BYTES: Final = 1024 * 1024


def _opens_like_sse(head: bytes) -> bool | None:
    """Whether a raw stream is SSE, judged by its opening bytes; None while undecidable.

    An SSE stream opens with a field name or a `:` comment. Anything else -- a JSON array
    streamed in pieces, say -- has no event boundaries to wait for. A chunk that ends
    partway through a field name decides nothing yet, so that case waits for more.
    """
    opening: Final = head.lstrip()
    if not opening:
        return None
    if opening.startswith(_SSE_OPENINGS):
        return True
    if any(field.startswith(opening) for field in _SSE_OPENINGS):
        return None
    return False


def _responses_event_type(chunk: object) -> str | None:
    """The event type of a Responses API stream event, or None for any other chunk.

    The type arrives as a plain string on dicts and as a str-valued Enum on LiteLLM's
    event models. The Enum is unwrapped because it does not hash like its value.
    """
    if isinstance(chunk, (bytes, str)):
        return None
    kind: Final = _read_field(chunk, "type")
    value: Final = kind.value if isinstance(kind, Enum) else kind
    return value if isinstance(value, str) and value.startswith("response.") else None


def _responses_stream_key(event: object, kind: str) -> tuple:
    """Identifies the delta stream an event belongs to, the same for its delta and done.

    The family is the event type without its `.delta` / `.done` suffix, so an output_text
    stream and a refusal stream on the same part never share a window.
    """
    family: Final = kind.rsplit(".", 1)[0]
    part_index: Final = _read_field(event, "content_index")
    summary_index: Final = _read_field(event, "summary_index")
    return (
        family,
        _read_field(event, "item_id"),
        _read_field(event, "output_index"),
        part_index if part_index is not None else summary_index,
    )


def _collect_event_text(event: object, slots: _SlotSink) -> None:
    """Collects every top-level text field of a Responses API event, dict or model.

    Scan by default, with identifiers excluded, rather than a list of known fields: the
    `.done` event of each stream family names its text differently (`text`, `refusal`,
    `arguments`, ...), and a family added upstream would otherwise leak a placeholder.
    """
    fields: Final = event if isinstance(event, dict) else getattr(event, "__dict__", None)
    if not isinstance(fields, dict):
        return
    for name, value in tuple(fields.items()):
        if name in _RESPONSES_STRUCTURAL_FIELDS or name.endswith("_id"):
            continue
        if isinstance(value, str) and value:
            slots.append((value, functools.partial(_write_field, event, name)))


class _AnthropicSSERestorer:
    """Restores an Anthropic `/v1/messages` stream, which reaches the hook as raw SSE.

    Each content block is its own token stream with its own window, keyed by the block's
    `index`: `text_delta` carries prose and `input_json_delta` a tool call's arguments.
    When a block stops, whatever its window still holds is emitted as one more delta for
    that block, just ahead of the `content_block_stop` frame, so the client has the whole
    block before it is told the block is complete.

    Frames are processed whole. A network chunk can end in the middle of an event, so the
    unfinished tail is kept until the rest arrives; that delays one partial event, never
    a completed one. A frame that is not an Anthropic event -- another endpoint's SSE, or
    anything that fails to parse -- is passed through byte for byte, and a raw stream that
    does not open like SSE at all is passed through chunk by chunk, never buffered.
    """

    def __init__(self, step: _StreamStep, guardrail_name: str = GUARDRAIL_NAME) -> None:
        self._step: Final = step
        self._guardrail_name: Final = guardrail_name
        self._carries: Final[dict] = {}  # mutable-ok: per-block windows advanced in place.
        self._delta_types: Final[dict] = {}  # mutable-ok: each block's delta type, for its flush.
        self._pending = b""
        self._as_text = False
        self._is_sse: bool | None = None

    async def feed(self, chunk: bytes | str) -> tuple:
        """Restores every event this chunk completes; holds back an unfinished tail."""
        if isinstance(chunk, str):
            self._as_text = True
        if self._is_sse is False:
            return (chunk,)
        raw: Final = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
        buffered: Final = self._pending + raw
        if self._is_sse is None:
            self._is_sse = _opens_like_sse(buffered)
            if self._is_sse is None:
                self._pending = buffered
                return ()
            if not self._is_sse:
                self._pending = b""
                return self._emit(buffered)
        boundaries: Final = tuple(_SSE_EVENT_BOUNDARY.finditer(buffered))
        if not boundaries:
            if len(buffered) > _MAX_SSE_PENDING_BYTES:
                # Fail closed, as the core package does: passing the bytes through would hand
                # the client an unrestored stream, and holding them would grow without bound.
                self._pending = b""
                raise GuardrailRaisedException(
                    guardrail_name=self._guardrail_name,
                    message=(
                        f"LLM Shield Proxy: an SSE event exceeded {_MAX_SSE_PENDING_BYTES} bytes "
                        "without an event boundary; blocking the stream."
                    ),
                )
            self._pending = buffered
            return ()
        cut: Final = boundaries[-1].end()
        self._pending = buffered[cut:]
        parts: Final = _SSE_EVENT_BOUNDARY.split(buffered[:cut])
        restored: Final = tuple(
            [await self._restore_event(parts[index]) + parts[index + 1] for index in range(0, len(parts) - 1, 2)]
        )
        return self._emit(b"".join(restored))

    async def finish(self) -> tuple:
        """Emits an unterminated final event and any window a block never closed."""
        held: Final = self._pending
        self._pending = b""
        if not self._is_sse:
            return self._emit(held)
        tail: Final = await self._restore_event(held) if held.strip() else held
        flushed: Final = await self._flush_all()
        separator: Final = b"\n\n" if tail.strip() and flushed else b""
        return self._emit(tail + separator + flushed)

    def _emit(self, frames: bytes) -> tuple:
        if not frames:
            return ()
        return (frames.decode("utf-8") if self._as_text else frames,)

    async def _restore_event(self, block: bytes) -> bytes:
        """Rewrites one SSE event, or returns it untouched if it carries nothing to restore."""
        try:
            lines: Final = block.decode("utf-8").split("\n")
        except UnicodeDecodeError:
            return block
        data_lines: Final = tuple(index for index, line in enumerate(lines) if line.startswith("data:"))
        if len(data_lines) != 1:
            return block
        line: Final = lines[data_lines[0]]
        try:
            event: Final = json.loads(line[len("data:") :])
        except ValueError:
            return block
        if not isinstance(event, dict):
            return block
        kind: Final = event.get("type")
        index: Final = event.get("index")
        if kind == "content_block_stop" and isinstance(index, int):
            return await self._flush(index) + block
        if kind == "message_stop":
            return await self._flush_all() + block
        if kind != "content_block_delta" or not await self._restore_delta(event):
            return block
        ending: Final = "\r" if line.endswith("\r") else ""
        rewritten: Final = (
            *lines[: data_lines[0]],
            f"data: {json.dumps(event, ensure_ascii=False)}{ending}",
            *lines[data_lines[0] + 1 :],
        )
        return "\n".join(rewritten).encode("utf-8")

    async def _restore_delta(self, event: MutableRequest) -> bool:
        """Advances one block's window through this delta. False if it holds no text."""
        index: Final = event.get("index")
        delta: Final = event.get("delta")
        if not isinstance(index, int) or not isinstance(delta, dict):
            return False
        delta_type: Final = delta.get("type")
        if not isinstance(delta_type, str):
            return False
        field: Final = _ANTHROPIC_DELTA_FIELDS.get(delta_type)
        text: Final = delta.get(field) if field is not None else None
        if field is None or not isinstance(text, str) or not text:
            return False
        emitted, remaining = await self._step(text, self._carries.get(index, ""), False)
        self._carries[index] = remaining
        self._delta_types[index] = delta_type
        delta[field] = emitted
        return True

    async def _flush(self, index: int) -> bytes:
        """One synthetic delta frame carrying whatever `index`'s window still holds."""
        carry: Final = self._carries.pop(index, "")
        delta_type: Final = self._delta_types.pop(index, None)
        field: Final = _ANTHROPIC_DELTA_FIELDS.get(delta_type) if isinstance(delta_type, str) else None
        if not carry or field is None:
            return b""
        text, _ = await self._step("", carry, True)
        if not text:
            return b""
        event: Final[JsonBody] = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": delta_type, field: text},
        }
        return f"event: content_block_delta\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()

    async def _flush_all(self) -> bytes:
        flushed: Final = tuple([await self._flush(index) for index in tuple(self._carries)])
        return b"".join(flushed)


class _ResponsesStreamRestorer:
    """Restores a Responses API event stream.

    The event families are matched by shape rather than listed, so a text stream the
    API adds later is restored by default instead of leaking a placeholder:

    - Any `*.delta` event whose `delta` is a string is a token stream (output_text,
      refusal, function-call and MCP arguments, reasoning summaries, ...). Each gets its
      own window, keyed by the family, the item id and the part index.
    - Any `*.done` event closes the stream of the same family. Whatever its window still
      holds goes out first, as a copy of that stream's last delta event -- so it carries
      the stream's own ids, and repeats that event's `sequence_number`. Then every text
      field on the done event is restored in full: its string fields other than
      identifiers, plus any `part` or `item` it repeats.
    - `response.completed` / `response.incomplete` repeat the whole reply, and are
      restored the same way the non-streaming reply is.
    """

    def __init__(self, step: _StreamStep, rehydrate: _Rehydrate) -> None:
        self._step: Final = step
        self._rehydrate: Final = rehydrate
        self._carries: Final[dict] = {}  # mutable-ok: per-stream windows advanced in place.
        self._last_deltas: Final[dict] = {}  # mutable-ok: newest delta per stream.

    async def restore(self, event: object) -> tuple:
        """The events to emit in place of `event`: any flush, then the event itself."""
        kind: Final = _responses_event_type(event)
        if kind is None:
            return (event,)
        if kind.endswith(".delta") and kind not in _RESPONSES_BINARY_DELTAS:
            await self._restore_delta(event, kind)
            return (event,)
        slots: Final[list] = []  # mutable-ok: accumulator, frozen before use.
        flushed: Final = await self._flush(_responses_stream_key(event, kind)) if kind.endswith(".done") else ()
        if kind.endswith(".done"):
            _collect_event_text(event, slots)
            part: Final = _read_field(event, "part")
            if part is not None:
                _collect_response_item({"content": [part]}, slots)
            _collect_response_item(_read_field(event, "item"), slots)
        elif kind in _RESPONSES_TERMINAL_EVENTS:
            for item in _read_list(_read_field(event, "response"), "output"):
                _collect_response_item(item, slots)
        if slots:
            restored: Final = await self._rehydrate(tuple(text for text, _ in slots))
            for (_, write), replacement in zip(slots, restored):
                write(replacement)
        return (*flushed, event)

    async def finish(self) -> tuple:
        """Flushes every stream the provider never closed, e.g. a truncated reply."""
        flushed: Final = tuple([await self._flush(key) for key in tuple(self._carries)])
        return tuple(itertools.chain.from_iterable(flushed))

    async def _restore_delta(self, event: object, kind: str) -> None:
        text: Final = _read_field(event, "delta")
        if not isinstance(text, str) or not text:
            return
        key: Final = _responses_stream_key(event, kind)
        emitted, remaining = await self._step(text, self._carries.get(key, ""), False)
        self._carries[key] = remaining
        self._last_deltas[key] = event
        _write_field(event, "delta", emitted)

    async def _flush(self, key: tuple) -> tuple:
        carry: Final = self._carries.pop(key, "")
        template: Final = self._last_deltas.pop(key, None)
        if not carry or template is None:
            return ()
        text, _ = await self._step("", carry, True)
        if not text:
            return ()
        flush: Final = copy.deepcopy(template)
        _write_field(flush, "delta", text)
        return (flush,)


def _is_text_choice(choice: object) -> bool:
    """A Completions choice: a `text` field and no `delta` (LiteLLM's TextChoices).

    Judged by the field's presence, not its value: the chunk that carries the
    finish_reason arrives with `text` set to None, and it is the chunk the held-back tail
    has to flush into.
    """
    if getattr(choice, "delta", None) is not None:
        return False
    if isinstance(choice, dict):
        return "text" in choice
    return hasattr(choice, "text")


class LLMShieldProxyGuardrail(CustomGuardrail):
    """Redacts PII before it leaves the proxy and restores it in the response.

    Unlike a masking guardrail, the substitution is reversible. Outbound text is
    replaced with placeholders held in a session vault inside the user's own LLM
    Shield deployment; the model's reply is then restored so the end user sees the
    original values while the provider never received them.

    Streaming is restored incrementally rather than by buffering the response. LLM
    Shield holds back only the trailing characters that could still turn out to be
    part of a placeholder, so tokens are forwarded as they arrive and a placeholder
    split across two chunks is never emitted in fragments.
    """

    # Our redaction and restoration run in the native lifecycle hooks below. Without
    # this the proxy would route every event through the unified apply_guardrail path
    # and the streaming hook would never fire.
    use_native_lifecycle_hooks: ClassVar[bool] = True

    def __init__(
        self,
        guardrail_name: str = GUARDRAIL_NAME,
        api_base: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,  # noqa: LIT008  # kwargs-ok: forwarded verbatim to CustomGuardrail.__init__
    ) -> None:
        self.async_handler = get_async_httpx_client(llm_provider=httpxSpecialProvider.GuardrailCallback)
        env_base: Final = os.environ.get("LLM_SHIELD_PROXY_API_BASE")
        self.api_base: Final = (api_base or env_base or _DEFAULT_API_BASE).rstrip("/")
        self.api_key: Final = api_key or os.environ.get("LLM_SHIELD_PROXY_API_KEY")
        super().__init__(guardrail_name=guardrail_name, **kwargs)

    @classmethod
    def get_supported_event_hooks(cls) -> list[GuardrailEventHooks]:  # mutable-ok: parent's signature.
        return [GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call]  # mutable-ok: parent's signature.

    # --- transport ---------------------------------------------------------------

    def _headers(self, session_id: str) -> JsonBody:
        headers: Final[JsonBody] = {  # mutable-ok: httpx requires a real dict.
            "Content-Type": "application/json",
            "X-Session-ID": session_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _call_shield(self, path: str, session_id: str, payload: JsonBody) -> Mapping[str, object]:
        """Posts to LLM Shield Proxy, failing closed on any transport or status error.

        A redaction guardrail that fails open sends the very data it exists to
        protect to a third-party provider, so an unreachable or erroring shield
        blocks the request instead of passing it through.
        """
        try:
            response: Final = await self.async_handler.post(
                f"{self.api_base}{path}",
                headers=self._headers(session_id),
                json=payload,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            verbose_proxy_logger.exception("LLM Shield Proxy returned %s for %s", exc.response.status_code, path)
            raise GuardrailRaisedException(
                guardrail_name=self.guardrail_name,
                message=f"LLM Shield Proxy returned {exc.response.status_code}; blocking the request.",
            ) from exc
        except Exception as exc:
            verbose_proxy_logger.exception("LLM Shield Proxy call to %s failed", path)
            raise GuardrailRaisedException(
                guardrail_name=self.guardrail_name,
                message="LLM Shield Proxy is unreachable; blocking the request.",
            ) from exc

    async def _redact(self, texts: Sequence[str], session_id: str) -> Sequence[str]:
        payload: Final[JsonBody] = {"texts": list(texts)}  # mutable-ok: JSON body for httpx.
        body: Final = await self._call_shield(_REDACT_PATH, session_id, payload)
        return self._same_length_or_raise(body.get("texts"), texts, "redact")

    async def _rehydrate(self, texts: Sequence[str], session_id: str) -> Sequence[str]:
        payload: Final[JsonBody] = {"texts": list(texts)}  # mutable-ok: JSON body for httpx.
        body: Final = await self._call_shield(_REHYDRATE_PATH, session_id, payload)
        return self._same_length_or_raise(body.get("texts"), texts, "rehydrate")

    def _same_length_or_raise(self, returned: object, sent: Sequence[str], operation: str) -> Sequence[str]:
        """Guards the positional mapping the callers rely on to write results back."""
        if not isinstance(returned, list) or len(returned) != len(sent):
            raise GuardrailRaisedException(
                guardrail_name=self.guardrail_name,
                message=f"LLM Shield Proxy {operation} returned an unexpected payload; blocking the request.",
            )
        return tuple(returned)

    # --- session ------------------------------------------------------------------

    @staticmethod
    def _mint_session_id(data: MutableRequest) -> str:
        """Mints a vault id for this request, overwriting anything already there.

        Redaction and restoration both happen inside one request/response pair, so
        a fresh id per request is all that is needed, and it is what keeps one
        caller from reaching another caller's vault.
        """
        session_id: Final = f"{_VAULT_PREFIX}-{uuid.uuid4().hex}"
        metadata: Final = data.setdefault("metadata", {})  # mutable-ok: per-request store.
        if isinstance(metadata, dict):
            metadata[_SESSION_METADATA_KEY] = session_id
        return session_id

    @staticmethod
    def _session_id(data: MutableRequest) -> str:
        """Reads back the vault id minted while redacting this request.

        Falls back to an unused id rather than to anything the caller supplied: a
        reply that cannot be restored is a visible placeholder, while trusting a
        caller-supplied id would hand them someone else's plaintext.
        """
        metadata: Final = data.get("metadata")
        existing: Final = metadata.get(_SESSION_METADATA_KEY) if isinstance(metadata, dict) else None
        if isinstance(existing, str) and existing.startswith(_VAULT_PREFIX):
            return existing
        return f"{_VAULT_PREFIX}-{uuid.uuid4().hex}"

    # --- request traversal --------------------------------------------------------

    @staticmethod
    def _locate_request_texts(
        data: MutableRequest,
    ) -> tuple[Sequence[_Slot], Sequence[_Slot]]:
        """Finds every redactable span, split by whether the caller can see it.

        Anything missed here reaches the provider in the clear while the guardrail
        still reports as enabled, so the walk covers every request shape that
        carries text.

        The split exists because the response is restored against one vault only.
        Server-authored spans -- system and developer turns, Anthropic's top-level
        `system`, the Responses API `instructions` -- go into a vault nothing is
        ever restored against, so a caller who gets the model to echo one of their
        placeholders back receives the placeholder, not the value behind it.
        """
        slots: Final[_SlotSink] = []  # mutable-ok: accumulator, frozen on return.
        privileged: Final[_SlotSink] = []  # mutable-ok: accumulator, frozen on return.
        for message in data.get("messages") or ():
            if isinstance(message, dict):
                sink = privileged if message.get("role") in _PRIVILEGED_ROLES else slots
                _collect_content(message, sink)
                _collect_participant_name(message, sink)
                _collect_tool_arguments(message, sink)
        _collect_responses_fields(data, slots, privileged)
        _collect_prompt(data, slots)
        _collect_system(data, privileged)
        return tuple(slots), tuple(privileged)

    # --- hooks --------------------------------------------------------------------

    @log_guardrail_information
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: "DualCache",
        data: MutableRequest,
        call_type: str,
    ) -> MutableRequest | None:
        """Replaces PII anywhere in the outbound request with vault placeholders."""
        if self.should_run_guardrail(data=data, event_type=GuardrailEventHooks.pre_call) is not True:
            return data

        slots, privileged = self._locate_request_texts(data)
        if not slots and not privileged:
            return data

        session_id: Final = self._mint_session_id(data)
        if privileged:
            # A vault of its own, whose id is deliberately never stored: the
            # response is restored against `session_id` alone, so nothing the
            # model emits can turn one of these placeholders back into plaintext.
            await self._redact_into(privileged, f"{_VAULT_PREFIX}-{uuid.uuid4().hex}")
        if slots:
            await self._redact_into(slots, session_id)
        return data

    async def _redact_into(self, slots: Sequence[_Slot], session_id: str) -> None:
        """Redacts every span in `slots` under one vault and writes the result back."""
        redacted: Final = await self._redact(tuple(text for text, _ in slots), session_id)
        for (_, write), replacement in zip(slots, redacted):
            write(replacement)

    # KNOWN LIMIT: a tool call's `arguments` is a JSON *string*, and a restored value is
    # spliced into it as raw text. If the original value contained a double quote, a
    # backslash or a newline, the reassembled document is no longer valid JSON for a
    # strict parser. The proxy's own tool-argument rehydration has the same property
    # (`_rehydrate_json_response` in api/main.py), so this is a pre-existing limit of the
    # product rather than one introduced here. Escaping is deliberately NOT applied as a
    # fix: a fragment is an arbitrary slice of a JSON document, so the code cannot tell
    # whether the position it writes is inside a string literal, and escaping
    # unconditionally would corrupt the values that are not.
    @log_guardrail_information
    async def async_post_call_success_hook(
        self,
        data: MutableRequest,
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
    ) -> Any:
        """Restores the original values in a non-streaming response."""
        if self.should_run_guardrail(data=data, event_type=GuardrailEventHooks.post_call) is not True:
            return response

        if self._is_anthropic_message_response(response):
            return await self._restore_anthropic_response(response, data)

        response_slots: Final = self._responses_api_slots(response)
        if response_slots:
            return await self._restore_responses_api_response(response, response_slots, data)

        choices: Final = getattr(response, "choices", None)
        if not choices:
            return response

        # One batch for every restorable span in the reply, collected in document order:
        # the shield maps its answers back by position. A second round trip is not an
        # option here -- /v1/guard/rehydrate caps a batch at 256 texts and 1,000,000
        # characters, and `_same_length_or_raise` is what guarantees the positional
        # mapping -- so a reply carrying more spans than that fails closed, which is this
        # guardrail's posture everywhere else.
        pending: Final[list] = []  # mutable-ok: local accumulator, frozen before use.
        for choice in choices:
            message: Final = getattr(choice, "message", None)
            if message is None:
                # `/v1/completions` answers with `text` on the choice and no message.
                text: Final = _read_field(choice, "text")
                if isinstance(text, str) and text:
                    pending.append((text, functools.partial(_write_field, choice, "text")))
                continue
            content: Final = getattr(message, "content", None)
            if isinstance(content, str) and content:
                pending.append((content, lambda new, m=message: setattr(m, "content", new)))
            # A tool call's `arguments` is model-generated text and the request path
            # redacts it, so leaving it unrestored hands the application a placeholder to
            # invoke a tool with. These are Pydantic objects on this path, not dicts.
            for tool_call in getattr(message, "tool_calls", None) or ():
                function: Final = getattr(tool_call, "function", None)
                arguments: Final = getattr(function, "arguments", None) if function is not None else None
                if isinstance(arguments, str) and arguments:
                    pending.append((arguments, lambda new, f=function: setattr(f, "arguments", new)))
            legacy: Final = getattr(message, "function_call", None)
            legacy_arguments: Final = getattr(legacy, "arguments", None) if legacy is not None else None
            if isinstance(legacy_arguments, str) and legacy_arguments:
                pending.append((legacy_arguments, lambda new, fn=legacy: setattr(fn, "arguments", new)))
        if not pending:
            return response

        restored: Final = await self._rehydrate(tuple(text for text, _ in pending), self._session_id(data))
        for (_, write), replacement in zip(pending, restored):
            write(replacement)
        return response

    @staticmethod
    def _is_anthropic_message_response(response: object) -> bool:
        """Anthropic's native /v1/messages reply arrives as a plain dict."""
        return (
            isinstance(response, dict)
            and response.get("type") == "message"
            and isinstance(response.get("content"), list)
        )

    async def _restore_anthropic_response(self, response: MutableRequest, data: MutableRequest) -> MutableRequest:
        """Restores text blocks and tool inputs in an Anthropic native message reply.

        This shape has no `choices`, so without its own branch the reply would go
        back to the caller still carrying placeholders.

        A `tool_use` block's payload is `input`, an arbitrary JSON object rather than a
        string, and the request path redacts its string leaves -- so the reply's leaves
        have to come back or the application invokes the tool with placeholders.
        """
        slots: Final[list] = []  # mutable-ok: accumulator, frozen before use.
        for block in response["content"]:
            if not isinstance(block, dict):
                continue
            kind: Final = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str) and block["text"]:
                slots.append((block["text"], lambda new, b=block: b.__setitem__("text", new)))
            elif kind == "tool_use" and isinstance(block.get("input"), dict):
                _collect_json_leaves(block["input"], slots)
        if not slots:
            return response

        restored: Final = await self._rehydrate(tuple(text for text, _ in slots), self._session_id(data))
        for (_, write), replacement in zip(slots, restored):
            write(replacement)
        return response

    @staticmethod
    def _responses_api_slots(response: object) -> Sequence[_Slot]:
        """Restorable spans in a Responses API reply.

        That shape carries `output` items rather than `choices`, so it needs its own
        walk; without one the reply goes back to the caller still holding
        placeholders even though the request was redacted correctly. Items and blocks
        come through as dicts or as objects depending on how far the reply has been
        deserialised, so both are handled.

        The item-level fields mirror `_collect_responses_fields`, which walks the same
        fields on the request side -- a function_call item holds `arguments`, a
        function_call_output holds `output` -- so the two directions stay symmetric.
        """
        slots: Final[list] = []  # mutable-ok: accumulator, frozen on return.
        for item in getattr(response, "output", None) or ():
            _collect_response_item(item, slots)
        return tuple(slots)

    async def _restore_responses_api_response(
        self, response: Any, slots: Sequence[_Slot], data: MutableRequest
    ) -> Any:
        """Puts the original values back into a Responses API reply."""
        restored: Final = await self._rehydrate(tuple(text for text, _ in slots), self._session_id(data))
        for (_, write), replacement in zip(slots, restored):
            write(replacement)
        return response

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
        request_data: MutableRequest,
    ) -> AsyncGenerator[Any, None]:
        """Restores original values incrementally, without buffering the stream.

        Each choice -- and each tool call within a choice -- is its own token stream, so
        the sliding window is tracked per (choice index, tool call) pair. One shared
        window would splice the characters held back for one stream onto another. The
        windows are locals of this generator, so they are scoped to a single stream and
        cannot leak between concurrent requests.
        """
        if self.should_run_guardrail(data=request_data, event_type=GuardrailEventHooks.post_call) is not True:
            async for chunk in response:
                yield chunk
            return

        session_id: Final = self._session_id(request_data)
        step: Final = functools.partial(self._stream_step, session_id=session_id)
        rehydrate: Final = functools.partial(self._rehydrate, session_id=session_id)
        # The two native stream shapes have no `choices`: Anthropic `/v1/messages` arrives
        # as raw SSE frames, the Responses API as typed events. Each has its own restorer
        # with the same per-stream windows.
        sse: Final = _AnthropicSSERestorer(step, self.guardrail_name)
        events: Final = _ResponsesStreamRestorer(step, rehydrate)
        carries: Final[dict] = {}  # mutable-ok: per-stream windows, local to this generator.
        last_chunk = None  # rebind-ok: tracks the most recent chunk for the final flush.

        async for chunk in response:
            if isinstance(chunk, (bytes, str)):
                for frames in await sse.feed(chunk):
                    yield frames
                continue
            if _responses_event_type(chunk) is not None:
                for event in await events.restore(chunk):
                    yield event
                continue
            last_chunk = chunk
            for choice in getattr(chunk, "choices", None) or ():
                await self._restore_choice(choice, carries, session_id)
            yield chunk

        for frames in await sse.finish():
            yield frames
        for event in await events.finish():
            yield event
        # A stream that ended without a finish_reason can still leave text held back.
        if last_chunk is not None and any(carries.values()):
            async for trailing in self._flush_trailing(last_chunk, carries, session_id):
                yield trailing

    async def _restore_choice(self, choice: Any, carries: _CarryWindows, session_id: str) -> None:
        """Restores one choice's delta, advancing that choice's own windows.

        Content and each tool call are separate token streams, so each gets its own
        window: `(choice_index, None)` for content, `(choice_index, tool_call_index)` for
        one tool call's accumulating `arguments`. A shared window would splice the text
        held back for one stream onto another.
        """
        delta: Final = getattr(choice, "delta", None)
        index: Final = _choice_index(choice)
        is_final: Final = bool(getattr(choice, "finish_reason", None))
        if _is_text_choice(choice):
            await self._restore_text_window(choice, (index, None), carries, session_id, is_final)
            return
        if delta is None:
            return

        await self._restore_content_window(delta, (index, None), carries, session_id, is_final)

        for tool_call in getattr(delta, "tool_calls", None) or ():
            await self._restore_tool_call_window(tool_call, index, carries, session_id)

        if is_final:
            # A client parses a tool call's arguments when it sees the finish_reason, so
            # every window this choice still holds has to land in *this* chunk. Flushing
            # after it produces argument JSON the client has already stopped waiting for.
            await self._flush_finished_choice(delta, index, carries, session_id)

    async def _restore_text_window(
        self,
        choice: Any,
        key: tuple,
        carries: _CarryWindows,
        session_id: str,
        is_final: bool,
    ) -> None:
        """Restores a Completions stream choice's `text` through its window."""
        carry: Final = carries.get(key, "")
        text: Final = _read_field(choice, "text")
        if not isinstance(text, str) or not text:
            if not (is_final and carry):
                return
        emitted, remaining = await self._stream_step(text if isinstance(text, str) else "", carry, is_final, session_id)
        carries[key] = remaining  # rebind-ok: this stream's window advances.
        if emitted or text:
            _write_field(choice, "text", emitted)

    async def _restore_content_window(
        self,
        delta: Any,
        key: tuple,
        carries: _CarryWindows,
        session_id: str,
        is_final: bool,
    ) -> None:
        """Restores one delta's content through its own window."""
        carry: Final = carries.get(key, "")
        text: Final = getattr(delta, "content", None)

        if not isinstance(text, str) or not text:
            # Nothing to restore here, but a final chunk still has to flush the window.
            if is_final and carry:
                flushed, remaining = await self._stream_step("", carry, True, session_id)
                carries[key] = remaining  # rebind-ok: this stream's window advances.
                if flushed:
                    delta.content = flushed
            return

        emitted, remaining = await self._stream_step(text, carry, is_final, session_id)
        carries[key] = remaining  # rebind-ok: this stream's window advances.
        delta.content = emitted

    async def _restore_tool_call_window(
        self,
        tool_call: Any,
        choice_index: int,
        carries: _CarryWindows,
        session_id: str,
    ) -> None:
        """Restores one streamed tool call's argument fragment.

        A tool call's `arguments` is a JSON document delivered as fragments that clients
        concatenate per tool-call index, so each index gets a window of its own rather
        than sharing the content stream's.
        """
        tool_index: Final = _read_field(tool_call, "index")
        if not isinstance(tool_index, int):
            return
        function: Final = _read_field(tool_call, "function")
        if function is None:
            return
        arguments: Final = _read_field(function, "arguments")
        if not isinstance(arguments, str) or not arguments:
            return

        key: Final = (choice_index, tool_index)
        emitted, remaining = await self._stream_step(arguments, carries.get(key, ""), False, session_id)
        carries[key] = remaining  # rebind-ok: this tool call's window advances.
        _write_field(function, "arguments", emitted)

    async def _flush_finished_choice(
        self,
        delta: Any,
        choice_index: int,
        carries: _CarryWindows,
        session_id: str,
    ) -> None:
        """Emits everything this finishing choice still holds, into this chunk.

        A client parses a tool call's `arguments` when the chunk carrying the
        finish_reason arrives, so a flush delivered afterwards is too late -- the client
        has already tried to parse truncated JSON. Content lands back on `content`; held
        tool-call text is appended as an index-only continuation entry, which is the shape
        clients concatenate by index, so no id or name is needed. Appending is correct
        even when this chunk already carried a fragment for that tool call.
        """
        continuations: Final[list] = []  # mutable-ok: built into this chunk's delta.
        for key in sorted((held for held in carries if held[0] == choice_index), key=_carry_sort_key):
            carry = carries[key]
            if not carry:
                continue
            _, tool_index = key
            text, remaining = await self._stream_step("", carry, True, session_id)
            carries[key] = remaining  # rebind-ok: this stream's window advances.
            if not text:
                continue
            if tool_index is None:
                delta.content = text
            else:
                continuations.append({"index": tool_index, "function": {"arguments": text}})
        if continuations:
            existing: Final[list] = list(getattr(delta, "tool_calls", None) or [])
            delta.tool_calls = existing + continuations

    async def _flush_trailing(
        self, last_chunk: Any, carries: _CarryWindows, session_id: str
    ) -> AsyncGenerator[Any, None]:
        """Empties every window still holding text, one chunk per window.

        This is the net for a stream that ended with no finish_reason at all; a stream
        that ended with one is flushed into its own terminal chunk by
        `_flush_finished_choice`, because that is the moment a client parses tool
        arguments.

        Driven by the windows rather than by the last chunk's choices. A choice that
        finished earlier is not present in the terminal chunk, and flushing only what
        that chunk carries would drop its held text and truncate its answer.
        """
        for key in sorted(carries, key=_carry_sort_key):
            carry = carries[key]
            if not carry:
                continue
            choice_index, tool_index = key
            text, remaining = await self._stream_step("", carry, True, session_id)
            carries[key] = remaining  # rebind-ok: this stream's window advances.
            if not text:
                continue
            chunk = self._chunk_for_choice(last_chunk, choice_index)
            if chunk is None:
                continue
            if _is_text_choice(chunk.choices[0]):
                _write_field(chunk.choices[0], "text", text)
            elif tool_index is None:
                chunk.choices[0].delta.content = text
            else:
                # The copy carried this chunk's own content and tool calls, both already
                # delivered. Replace rather than append, and drop the content, or the
                # client sees them twice.
                chunk.choices[0].delta.content = None
                chunk.choices[0].delta.tool_calls = [{"index": tool_index, "function": {"arguments": text}}]
            yield chunk

    @staticmethod
    def _chunk_for_choice(last_chunk: Any, index: int) -> Any:
        """A single-choice copy of the last chunk, carrying only `index`.

        Emitting one choice per chunk keeps a flush from reading as content on a
        choice it does not belong to.
        """
        chunk: Final = last_chunk.model_copy(deep=True)
        raw_choices: Final = getattr(chunk, "choices", None)
        if not raw_choices:
            return None
        choices: Final[tuple] = tuple(raw_choices)
        matching: Final = tuple(choice for choice in choices if _choice_index(choice) == index)
        kept: Final = matching[0] if matching else choices[0]
        if getattr(kept, "delta", None) is None and not _is_text_choice(kept):
            return None
        kept.index = index
        # The terminal signal, if there was one, already went out with the real chunk.
        kept.finish_reason = None
        chunk.choices = [kept]  # mutable-ok: the chunk model requires a list.
        return chunk

    async def _stream_step(self, text: str, carry: str, final: bool, session_id: str) -> tuple[str, str]:
        """Returns ``(text safe to emit now, window still being held)``."""
        body: Final = await self._call_shield(
            _REHYDRATE_STREAM_PATH,
            session_id,
            # mutable-ok: JSON request body for httpx.
            {"text": text, "carry": carry, "final": final},  # mutable-ok: JSON request body for httpx.
        )
        emitted: Final = body.get("text")
        remaining: Final = body.get("carry")
        if not isinstance(emitted, str) or not isinstance(remaining, str):
            raise GuardrailRaisedException(
                guardrail_name=self.guardrail_name,
                message="LLM Shield Proxy stream rehydration returned an unexpected payload.",
            )
        return emitted, remaining

    # --- unified API (powers the UI "Test guardrail" button) -----------------------

    @log_guardrail_information
    async def apply_guardrail(
        self,
        inputs: GenericGuardrailAPIInputs,
        request_data: MutableRequest,
        input_type: Literal["request", "response"],
        logging_obj: Optional["LiteLLMLoggingObj"] = None,
    ) -> GenericGuardrailAPIInputs:
        """Unified entry point: what the UI's Test guardrail button and the translation
        handlers call.

        `tool_calls` is handled on the response side only. LiteLLM populates the field here,
        and on a reply it holds the model's tool arguments -- the same text the native hook
        restores, and restoring one but not the other would leave the placeholder on
        whichever path ran. The request side is left to the native pre-call hook, because
        redacting it here as well would redact it twice.
        """
        text_list: Final[list] = list(inputs.get("texts") or ())
        tool_calls: Final[list] = list(inputs.get("tool_calls") or ()) if input_type == "response" else []
        if not text_list and not tool_calls:
            return inputs

        # Copied rather than mutated: the caller's tool calls are theirs to own, and this
        # method's contract is to hand back a new mapping.
        restored_calls: Final[list] = [copy.deepcopy(call) for call in tool_calls]  # mutable-ok: a new list.
        spans: Final[list] = list(text_list)  # mutable-ok: ordered batch, frozen before the call.
        writers: Final[list] = []  # mutable-ok: one per span appended below.
        for call in restored_calls:
            function: Final = _read_field(call, "function")
            arguments: Final = _read_field(function, "arguments") if function is not None else None
            if isinstance(arguments, str) and arguments:
                spans.append(arguments)
                writers.append(lambda new, f=function: _write_field(f, "arguments", new))

        replaced: Final = (
            await self._redact(tuple(spans), self._mint_session_id(request_data))
            if input_type == "request"
            else await self._rehydrate(tuple(spans), self._session_id(request_data))
        )
        restored_values: Final[list] = list(replaced)

        for write, replacement in zip(writers, restored_values[len(text_list) :]):
            write(replacement)
        # Return a new mapping rather than rewriting the caller's, so this stays a
        # pure transform of the inputs it was handed.
        merged: Final[JsonBody] = {**inputs}  # mutable-ok: TypedDict.
        if text_list:
            merged["texts"] = restored_values[: len(text_list)]
        if restored_calls:
            merged["tool_calls"] = restored_calls
        return merged
