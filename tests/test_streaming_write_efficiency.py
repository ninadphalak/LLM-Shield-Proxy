"""Pin structural SSE hot-path, memory, and cancellation invariants.

The old path yielded every parsed line independently and opened one OpenTelemetry span
per content delta. The current path coalesces bounded pieces produced from an upstream
chunk and emits one span per stream. These tests deliberately make no latency claim:
the earlier diagnostic runner and raw samples were not retained. They instead verify
observable events, incremental/drip delivery, cross-delta rehydration, expansion limits,
bounded references, and cancellation cleanup.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.security import attestation as attestation_module
from llm_shield_proxy.streaming import streaming as streaming_module
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

EVENTS = 64


def _sse_bytes(count: int) -> bytes:
    line = "data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}) + "\n\n"
    return (line * count + "data: [DONE]\n\n").encode()


async def _collect(chunks: list[bytes], vault: Vault | None = None) -> list[bytes]:
    async def source():
        for chunk in chunks:
            yield chunk

    out: list[bytes] = []
    stream = rehydrate_sse_stream(source(), vault or Vault(), path="v1/chat/completions")
    async for piece in stream:
        out.append(piece)
    return out


def _events_of(raw: bytes) -> list:
    """The SSE events a client would see, independent of write boundaries.

    Deliberately not a byte comparison against the INPUT: the pipeline re-serialises
    every data line, so JSON separators legitimately differ from whatever the upstream
    sent. What must hold is that the same events come out.
    """
    seen: list = []
    for line in raw.decode("utf-8").split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        body = line[6:]
        seen.append(body if body == "[DONE]" else json.loads(body))
    return seen


def _mapped_vault(token: str, original: str) -> Vault:
    vault = Vault(synthetic=False)
    vault.token_to_original[token] = original
    vault.original_to_token[original] = token
    vault.max_token_length = len(token)
    return vault


def test_one_upstream_chunk_is_not_split_into_one_write_per_line():
    """The regression. Two writes per event is the shape that measured 22x slow.

    Reverting the coalescing makes this about two writes per event and this fails.
    """
    payload = _sse_bytes(EVENTS)
    emitted = asyncio.run(_collect([payload]))
    assert len(emitted) < EVENTS, (
        f"{len(emitted)} writes for {EVENTS} events: the pipeline is yielding per line"
    )


def test_coalescing_changes_framing_and_nothing_else():
    """Framing moved; the events did not. This is what makes the change safe."""
    payload = _sse_bytes(EVENTS)
    emitted = asyncio.run(_collect([payload]))
    assert _events_of(b"".join(emitted)) == _events_of(payload)


def test_post_rehydration_aggregation_is_bounded(monkeypatch):
    """Several expanded events cannot inherit the raw chunk's smaller size."""
    budget = 512
    monkeypatch.setattr(streaming_module.settings, "MAX_SSE_LINE_LENGTH", budget)
    monkeypatch.setattr(streaming_module.settings, "MAX_PAYLOAD_SIZE_BYTES", 4096)

    token = "[PERSON_1]"
    original = "A" * 200
    vault = _mapped_vault(token, original)
    raw = (
        "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": token}}]}) + "\n\n"
            for _ in range(4)
        )
        + "data: [DONE]\n\n"
    ).encode()
    assert len(raw) < budget  # the pre-rehydration line guard does not prove this test

    emitted = asyncio.run(_collect([raw], vault))
    assert len(emitted) > 1
    assert max(map(len, emitted)) <= budget
    expected = (
        "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": original}}]}) + "\n\n"
            for _ in range(4)
        )
        + "data: [DONE]\n\n"
    ).encode()
    assert _events_of(b"".join(emitted)) == _events_of(expected)


def test_coalescer_also_bounds_reference_count():
    """Tiny lines cannot turn a byte-bounded aggregate into a huge object list."""
    coalescer = streaming_module._BoundedOutputCoalescer(1024 * 1024)

    for _ in range(coalescer._MAX_PARTS):
        assert coalescer.push(b"x") == ()

    flushed = coalescer.push(b"x")
    assert flushed == (b"x" * coalescer._MAX_PARTS,)
    assert coalescer.drain() == (b"x",)


def test_one_value_larger_than_coalescing_budget_is_returned_intact(monkeypatch):
    """The write target is not a truncation limit for request-bounded PII."""
    budget = 256
    monkeypatch.setattr(streaming_module.settings, "MAX_SSE_LINE_LENGTH", budget)
    monkeypatch.setattr(streaming_module.settings, "MAX_PAYLOAD_SIZE_BYTES", 4096)

    token = "[PERSON_1]"
    original = "Long PII value: " + ("x" * 2048)
    vault = _mapped_vault(token, original)
    raw = (
        "data: " + json.dumps({"choices": [{"delta": {"content": token}}]})
        + "\n\ndata: [DONE]\n\n"
    ).encode()

    emitted = asyncio.run(_collect([raw], vault))
    combined = b"".join(emitted)
    assert max(map(len, emitted)) > budget
    assert original.encode() in combined
    expected = (
        "data: " + json.dumps({"choices": [{"delta": {"content": original}}]})
        + "\n\ndata: [DONE]\n\n"
    ).encode()
    assert _events_of(combined) == _events_of(expected)


def test_repeated_token_amplification_fails_closed(monkeypatch):
    """A small upstream line cannot multiply request data without a ceiling."""
    monkeypatch.setattr(streaming_module.settings, "MAX_SSE_LINE_LENGTH", 512)
    monkeypatch.setattr(streaming_module.settings, "MAX_PAYLOAD_SIZE_BYTES", 1024)
    monkeypatch.setattr(streaming_module.settings, "SHIELD_FAILURE_MODE", "FAIL_CLOSED")

    token = "[PERSON_1]"
    vault = _mapped_vault(token, "A" * 700)
    amplified = " ".join([token] * 3)
    raw = (
        "data: " + json.dumps({"choices": [{"delta": {"content": amplified}}]})
        + "\n\ndata: [DONE]\n\n"
    ).encode()

    # Absolute ceiling is request bound + accepted input line: 1536 bytes.
    assert asyncio.run(_collect([raw], vault)) == []


def test_cancellation_does_not_flush_partial_rehydration(monkeypatch):
    """Cancellation leaves no partial token/output and still closes evidence state."""
    receipts: list[str] = []
    monkeypatch.setattr(
        attestation_module.StreamDigestReceipt,
        "emit_audit_receipt",
        lambda self: receipts.append(self.session_id),
    )

    token = "[PERSON_1]"
    vault = _mapped_vault(token, "Jane Roe")
    first_half = token[: len(token) // 2]

    async def scenario() -> bytes:
        blocker = asyncio.Event()

        async def source():
            line = "data: " + json.dumps(
                {"choices": [{"delta": {"content": first_half}}]}
            ) + "\n\n"
            yield line.encode()
            await blocker.wait()

        stream = rehydrate_sse_stream(source(), vault, path="v1/chat/completions")
        first = await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        return first

    first = asyncio.run(scenario())
    assert b"Jane Roe" not in first
    assert first_half.encode() not in first
    assert receipts == [vault.session_id]


def test_output_does_not_depend_on_how_the_upstream_frames_it():
    """The number of writes now depends on upstream chunking, so content must not."""
    payload = _sse_bytes(EVENTS)
    line = "data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}) + "\n\n"

    one_shot = b"".join(asyncio.run(_collect([payload])))
    per_event = b"".join(
        asyncio.run(_collect([line.encode()] * EVENTS + [b"data: [DONE]\n\n"]))
    )
    byte_at_a_time = b"".join(
        asyncio.run(_collect([payload[i : i + 1] for i in range(len(payload))]))
    )

    assert one_shot == per_event == byte_at_a_time
    assert _events_of(one_shot) == _events_of(payload)


def test_exactly_one_buffer_flush_span_per_stream(monkeypatch):
    """A span per delta is both expensive and unusable telemetry.

    Reverting fix 2 puts ``start_as_current_span`` back inside
    ``process_delta_text`` and this counts one span per event instead of one.
    """
    started: list[str] = []

    class _Span:
        def set_attribute(self, *_args, **_kwargs):
            return None

        def end(self, *_args, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Tracer:
        def start_span(self, name, *_args, **_kwargs):
            started.append(name)
            return _Span()

        def start_as_current_span(self, name, *_args, **_kwargs):
            started.append(name)
            return _Span()

    monkeypatch.setattr(streaming_module, "tracer", _Tracer())
    asyncio.run(_collect([_sse_bytes(EVENTS)]))

    flushes = [name for name in started if name == "buffer_flush"]
    assert len(flushes) == 1, (
        f"{len(flushes)} buffer_flush spans for {EVENTS} events; "
        "the span belongs to the stream, not to each delta"
    )


@pytest.mark.parametrize("chunking", ["one_shot", "per_event", "byte_at_a_time"])
def test_rehydration_still_spans_writes_after_coalescing(chunking):
    """A protected token split across deltas must still be reassembled.

    Coalescing changes when bytes leave, so the sliding window that joins a token
    across two deltas is exactly what it could have broken.
    """
    vault = Vault()
    token = vault.get_or_create_token("Jane Roe", "PERSON")
    halves = (token[: len(token) // 2], token[len(token) // 2 :])
    raw = (
        "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n"
            for piece in halves
        )
        + "data: [DONE]\n\n"
    ).encode()

    if chunking == "one_shot":
        chunks = [raw]
    elif chunking == "per_event":
        chunks = [part + b"\n\n" for part in raw.split(b"\n\n") if part]
    else:
        chunks = [raw[i : i + 1] for i in range(len(raw))]

    assert b"Jane Roe" in b"".join(asyncio.run(_collect(chunks, vault)))
