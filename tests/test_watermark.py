import json
import pytest
import asyncio
from pydantic import ValidationError
from collections.abc import AsyncGenerator

from llm_shield_proxy.core.config import Settings
from llm_shield_proxy.security.watermark import encode_steganography, generate_fingerprint, get_identity, generate_watermark_text
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream
from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault

# Import the decode function from the scripts directory
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.decode_watermark import decode_steganography


def test_steganography_math():
    """Verify that hex -> zero-width -> hex decoding works perfectly."""
    original_hex = "1a2b3c4d5e6f7a8b"
    zero_width = encode_steganography(original_hex)
    
    # Assert start and end characters
    assert zero_width.startswith("\u200D")
    assert zero_width.endswith("\u200D")
    
    # Each hex char = 4 bits, so 16 * 4 = 64 characters + 2 delimiters = 66
    assert len(zero_width) == 66
    
    decoded_result = decode_steganography(f"Some visible text {zero_width} and more text")
    assert original_hex in decoded_result


def test_config_fail_fast():
    """Verify that ENABLE_WATERMARKING without secret fails to boot."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENABLE_WATERMARKING=True,
            SHIELD_WATERMARK_SECRET=None,
            VALID_VIRTUAL_KEYS="test"
        )
    assert "SHIELD_WATERMARK_SECRET must be set" in str(exc.value)
    
    # Should work if set
    s = Settings(
        ENABLE_WATERMARKING=True,
        SHIELD_WATERMARK_SECRET="test-secret",
        VALID_VIRTUAL_KEYS="test"
    )
    assert s.ENABLE_WATERMARKING is True


@pytest.mark.asyncio
async def test_synthetic_final_chunk_injection():
    """Assert watermark is injected as a synthetic final chunk before [DONE]."""
    watermark_text = encode_steganography("deadbeef12345678")
    
    async def mock_stream() -> AsyncGenerator[bytes, None]:
        yield b'data: {"id": "test-id", "object": "chat.completion.chunk", "created": 1234, "model": "gpt-4", "choices":[{"delta":{"content":"Hello"}}]}\n\n'
        yield b'data: {"id": "test-id", "object": "chat.completion.chunk", "created": 1234, "model": "gpt-4", "choices":[{"delta":{"content":" World"}}]}\n\n'
        yield b'data: [DONE]\n\n'
        
    vault = StatelessCryptoVault()
    chunks = []
    async for chunk in rehydrate_sse_stream(mock_stream(), vault, watermark_text=watermark_text):
        chunks.append(chunk)
        
    # The chunks should be:
    # 0: data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n
    # 1: data: {"choices":[{"delta":{"content":" World"}}]}\n\n
    # 2: data: {"choices": [{"delta": {"content": "<WATERMARK>"}}]}\n\n  (Synthetic Chunk)
    # 3: data: [DONE]\n\n
    
    # Combine stream chunks back into logical chunks by splitting on \n\n
    joined = b"".join(chunks)
    logical_chunks = [c + b"\n\n" for c in joined.split(b"\n\n") if c]
    
    assert len(logical_chunks) == 4
    assert b"Hello" in logical_chunks[0]
    assert b" World" in logical_chunks[1]
    
    # Check synthetic chunk
    chunk_str = logical_chunks[2].decode("utf-8")
    assert watermark_text in chunk_str
    assert "choices" in chunk_str
    assert '"id":"test-id"' in chunk_str or '"id": "test-id"' in chunk_str
    assert '"model":"gpt-4"' in chunk_str or '"model": "gpt-4"' in chunk_str
    
    # Check final chunk is [DONE]
    assert logical_chunks[3] == b'data: [DONE]\n\n'


@pytest.mark.asyncio
async def test_watermark_not_injected_if_empty():
    """Assert no synthetic chunk is injected if watermark_text is empty."""
    async def mock_stream() -> AsyncGenerator[bytes, None]:
        yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        yield b'data: [DONE]\n\n'
        
    vault = StatelessCryptoVault()
    chunks = []
    async for chunk in rehydrate_sse_stream(mock_stream(), vault, watermark_text=""):
        chunks.append(chunk)
        
    joined = b"".join(chunks)
    logical_chunks = [c + b"\n\n" for c in joined.split(b"\n\n") if c]

    assert len(logical_chunks) == 2
    assert logical_chunks[1] == b'data: [DONE]\n\n'
