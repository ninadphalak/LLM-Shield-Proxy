import asyncio
import os

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer, rehydrate_sse_stream

try:
    import psutil
except ImportError:
    psutil = None


def test_extreme_split_tag_across_chunks():
    """
    Simulates a placeholder tag like [PERSON_1] deliberately shattered across 5+ consecutive tiny chunks:
    ['Hello ', '[', 'PE', 'RSON', '_1', ']', '! Welcome.']
    Verifies that the buffer holds partial brackets and only re-hydrates once the closing ']' tag arrives.
    """
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("John Doe", "PERSON")
    assert token == "[PERSON_1]"

    buffer = SSERehydrationBuffer(vault)

    # Shattered chunks
    chunks = ["Hello ", "[", "PE", "RSON", "_1", "]", "! Welcome."]
    emitted_parts = []

    for chunk in chunks:
        emitted = buffer.process_delta_text(chunk)
        emitted_parts.append(emitted)

    # Flush any remaining buffer at stream end
    final_flush = buffer.process_delta_text("", is_final=True)
    if final_flush:
        emitted_parts.append(final_flush)

    # Verify holding behavior: chunks 2-5 held back partial tag
    assert emitted_parts[0] == "Hello "
    assert emitted_parts[1] == ""  # '[' held
    assert emitted_parts[2] == ""  # '[PE' held
    assert emitted_parts[3] == ""  # '[PERSON' held
    assert emitted_parts[4] == ""  # '[PERSON_1' held
    assert emitted_parts[5] == "John Doe"  # Completed '[PERSON_1]' -> re-hydrated to 'John Doe'
    assert emitted_parts[6] == "! Welcome."

    full_output = "".join(emitted_parts)
    assert full_output == "Hello John Doe! Welcome."


def test_massive_streaming_response_memory_bounds():
    """
    Simulates a 10,000-token SSE streaming response to ensure the sliding-window buffer
    flushes continuously chunk-by-chunk and does not accumulate memory or cause a RAM spike.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("John Doe", "PERSON")
    buffer = SSERehydrationBuffer(vault)

    if psutil is not None:
        process = psutil.Process(os.getpid())
        initial_rss = process.memory_info().rss
    else:
        process = None
        initial_rss = 0

    total_chunks = 10000
    for i in range(total_chunks):
        chunk_text = f"Token_{i} [PERSON_1] chunk content. "
        buffer.process_delta_text(chunk_text)
        assert len(buffer.content_buffer) <= buffer.MAX_TAG_LENGTH

    buffer.process_delta_text("", is_final=True)
    assert buffer.content_buffer == ""

    if process is not None:
        final_rss = process.memory_info().rss
        rss_diff_mb = (final_rss - initial_rss) / (1024.0 * 1024.0)
        # Verify RAM usage remains tightly bounded (< 15 MB variance)
        assert rss_diff_mb < 15.0


def test_markdown_code_brackets_no_lockup():
    """
    Tests behavior when unclosed brackets appear in code blocks or markdown
    (e.g., Python lists `my_list = [1, 2, 3]` or long unclosed bracket text)
    to ensure the buffer releases content once MAX_TAG_LENGTH is exceeded.
    """
    vault = Vault(synthetic=False)
    buffer = SSERehydrationBuffer(vault)

    # Natural Python list bracket
    code_chunk_1 = "my_list = [1, 2, 3]\n"
    emitted_1 = buffer.process_delta_text(code_chunk_1)
    assert "my_list = [1, 2, 3]" in (emitted_1 + buffer.content_buffer)

    # Long unclosed bracket exceeding MAX_TAG_LENGTH (64 chars)
    long_bracket_text = "[" + ("x" * 80)
    emitted_2 = buffer.process_delta_text(long_bracket_text)

    # Because 80 chars > MAX_TAG_LENGTH (64), the buffer must NOT lock up or hold indefinitely
    assert len(emitted_2) > 0 or len(buffer.content_buffer) <= buffer.MAX_TAG_LENGTH

    flush = buffer.process_delta_text("", is_final=True)
    full_output = emitted_1 + emitted_2 + flush
    assert "[" + ("x" * 80) in full_output


@pytest.mark.asyncio
async def test_rehydrate_sse_stream_async_stress():
    """
    Tests async SSE stream rehydration generator under stream stress.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("Sarah Connor", "PERSON")

    async def mock_sse_stream():
        yield b'data: {"choices":[{"delta":{"content":"Contact [PER"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"SON_1] for privacy details."}}]}\n\n'
        yield b"data: [DONE]\n\n"

    rehydrated_chunks = []
    async for chunk_bytes in rehydrate_sse_stream(mock_sse_stream(), vault):
        rehydrated_chunks.append(chunk_bytes.decode("utf-8"))

    full_response = "".join(rehydrated_chunks)
    assert "Sarah Connor" in full_response


@pytest.mark.asyncio
async def test_rehydrate_sse_stream_concurrent_stress():
    """
    Flood the async generator with 500 simultaneous streaming connections
    to assert the underlying event loop remains non-blocking and processes correctly.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("Sarah Connor", "PERSON")

    async def single_stream_task():
        async def mock_sse_stream():
            yield b'data: {"choices":[{"delta":{"content":"Contact [PER"}}]}\n\n'
            # Simulating async wait in stream chunking
            await asyncio.sleep(0.01)
            yield b'data: {"choices":[{"delta":{"content":"SON_1] for privacy details."}}]}\n\n'
            yield b"data: [DONE]\n\n"

        rehydrated_chunks = []
        async for chunk_bytes in rehydrate_sse_stream(mock_sse_stream(), vault):
            rehydrated_chunks.append(chunk_bytes.decode("utf-8"))
        return "".join(rehydrated_chunks)

    # 500 simultaneous connections
    tasks = [single_stream_task() for _ in range(500)]
    results = await asyncio.gather(*tasks)

    for result in results:
        assert "Sarah Connor" in result
