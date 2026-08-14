"""Unit tests for Server-Sent Events (SSE) streaming rehydration and buffer boundaries."""

import pytest
from llm_shield_proxy.vault import Vault
from llm_shield_proxy.streaming import SSERehydrationBuffer, rehydrate_sse_stream


def test_sse_buffer_split_tag_handling():
    """Tests that placeholder tags split across SSE chunk boundaries are retained and flushed cleanly."""
    vault = Vault(synthetic=False)
    t_email = vault.get_or_create_token("sarah@skynet.com", "EMAIL")
    assert t_email == "[EMAIL_1]"

    buffer = SSERehydrationBuffer(vault)

    # Chunk 1 ends in a split tag "[EM"
    res1 = buffer.process_delta_text("Hello, contact info is [EM")
    assert res1 == "Hello, contact info is "  # "[EM" is held back in buffer

    # Chunk 2 finishes tag "AIL_1] now!"
    res2 = buffer.process_delta_text("AIL_1] now!")
    assert res2 == "sarah@skynet.com now!"

    # Final flush
    res3 = buffer.process_delta_text("", is_final=True)
    assert res3 == ""


def test_sse_buffer_synthetic_unbracketed_word_fragmentation():
    """Tests fragmented SSE chunks for synthetic unbracketed entities (e.g., 'Maya' or 'Sarah')."""
    vault = Vault(synthetic=True)
    # Register synthetic mapping manually into the vault
    vault.token_to_original["Maya"] = "OriginalSensitiveName"
    vault.original_to_token["OriginalSensitiveName"] = "Maya"
    vault.max_token_length = len("Maya")

    buffer = SSERehydrationBuffer(vault)

    # Fragmented chunks for 'Maya': ['Hello ', 'M', 'ay', 'a', '! How are you?']
    chunks = ["Hello ", "M", "ay", "a", "! How are you?"]
    emitted = []
    for chunk in chunks:
        out = buffer.process_delta_text(chunk)
        emitted.append(out)

    flush = buffer.process_delta_text("", is_final=True)
    if flush:
        emitted.append(flush)

    assert emitted[0] == "Hello "
    assert emitted[1] == ""  # 'M' held
    assert emitted[2] == ""  # 'May' held
    assert emitted[3] == "OriginalSensitiveName"  # Completed 'Maya' -> rehydrated
    assert emitted[4] == "! How are you?"

    full_output = "".join(emitted)
    assert full_output == "Hello OriginalSensitiveName! How are you?"
    assert "Maya" not in full_output


@pytest.mark.asyncio
async def test_rehydrate_sse_stream_generator():
    """Tests async generator rehydrating SSE stream with split tokens across JSON deltas."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_upstream_stream():
        yield b'data: {"choices":[{"delta":{"content":"User email is [EM"}}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"AIL_1] recorded."}}]}\n'
        yield b'data: [DONE]\n'

    output_bytes = []
    async for chunk in rehydrate_sse_stream(mock_upstream_stream(), vault):
        output_bytes.append(chunk.decode("utf-8"))

    full_output = "".join(output_bytes)
    assert "sarah@skynet.com" in full_output
    assert "[EMAIL_1]" not in full_output
