import pytest
from llm_shield_proxy.vault import Vault
from llm_shield_proxy.streaming import SSERehydrationBuffer, rehydrate_sse_stream


def test_sse_buffer_split_tag_handling():
    vault = Vault()
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


@pytest.mark.asyncio
async def test_rehydrate_sse_stream_generator():
    vault = Vault()
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_upstream_stream():
        # SSE line split across chunks
        yield b'data: {"choices":[{"delta":{"content":"User email is [EM"}}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"AIL_1] recorded."}}]}\n'
        yield b'data: [DONE]\n'

    output_bytes = []
    async for chunk in rehydrate_sse_stream(mock_upstream_stream(), vault):
        output_bytes.append(chunk.decode("utf-8"))

    full_output = "".join(output_bytes)
    assert "sarah@skynet.com" in full_output
    assert "[EMAIL_1]" not in full_output
