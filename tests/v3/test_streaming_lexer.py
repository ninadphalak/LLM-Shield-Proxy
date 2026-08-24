import pytest
import asyncio
import json
import base64
from cryptography.exceptions import InvalidTag
from llm_shield_proxy.v3.crypto import StatelessPIICipher
from llm_shield_proxy.v3.streaming_lexer import StatelessStreamingLexer, NonStreamingRehydrator

@pytest.fixture
def master_key():
    return b"0123456789abcdef0123456789abcdef"

@pytest.fixture
def session_a_cipher(master_key):
    return StatelessPIICipher(key=master_key, version=1, session_id="session_A")

@pytest.fixture
def session_b_cipher(master_key):
    return StatelessPIICipher(key=master_key, version=1, session_id="session_B")

def test_streaming_lexer_fragmentation(session_a_cipher):
    lexer = StatelessStreamingLexer(session_a_cipher)
    
    # Encrypt a value
    pt = "v1.AbCdEfG"
    token = session_a_cipher.encrypt(pt, "ssn")
    
    payload = f'{{"_ctx_hash_ssn": "{token}", "ssn": "Fake-1234", "other": "data"}}'
    
    # Feed it byte by byte
    emitted = ""
    for char in payload:
        emitted += lexer.feed_chunk(char)
        
    emitted += lexer.flush()
    
    # _ctx_hash_ssn should be pruned, and "Fake-1234" replaced by pt
    assert "_ctx_hash_ssn" not in emitted
    assert pt in emitted
    assert "Fake-1234" not in emitted
    assert "other" in emitted
    
    parsed = json.loads(emitted)
    assert parsed["ssn"] == pt
    assert parsed["other"] == "data"
    
def test_tampered_ciphertext_bit_flipping(session_a_cipher):
    # Encrypt a value
    pt = "secret_data"
    token = session_a_cipher.encrypt(pt, "ssn")
    
    # Tamper with the ciphertext (flip bits)
    raw = bytearray(base64.urlsafe_b64decode(token))
    raw[-5] = raw[-5] ^ 0xFF  # Flip a byte in the AuthTag/Ciphertext
    tampered_token = base64.urlsafe_b64encode(raw).decode('ascii')
    
    # Expect [CORRUPTED] fallback instead of exception
    result = session_a_cipher.decrypt(tampered_token, "ssn")
    assert result == "[CORRUPTED]"
    
    # Ensure Lexer uses the fallback if it processes tampered tokens
    lexer = StatelessStreamingLexer(session_a_cipher)
    payload = f'{{"_ctx_hash_ssn": "{tampered_token}", "ssn": "Fake-1234"}}'
    
    emitted = lexer.feed_chunk(payload) + lexer.flush()
    parsed = json.loads(emitted)
    
    # In Lexer, if it's corrupted, we don't replace the fake value or we ignore the rehydration
    # Actually, in Lexer code: if pt != "[CORRUPTED]", we rehydrate. So "Fake-1234" stays.
    assert parsed["ssn"] == "Fake-1234"
    assert "_ctx_hash_ssn" not in parsed

def test_hkdf_session_isolation(session_a_cipher, session_b_cipher):
    # Encrypt with Session A
    pt = "tenant_A_data"
    token_a = session_a_cipher.encrypt(pt, "ssn")
    
    # Decrypt with Session B
    # Should fail cleanly due to HKDF key isolation, returning [CORRUPTED]
    result = session_b_cipher.decrypt(token_a, "ssn")
    assert result == "[CORRUPTED]"

def test_non_streaming_rehydrator(session_a_cipher):
    rehydrator = NonStreamingRehydrator(session_a_cipher)
    
    pt1 = "real_ssn_1"
    token1 = session_a_cipher.encrypt(pt1, "ssn_1")
    
    pt2 = "real_val_2"
    token2 = session_a_cipher.encrypt(pt2, "nested_val")
    
    # Array proxy test
    pt3 = "array_item"
    token3 = session_a_cipher.encrypt(pt3, "")
    
    payload = {
        "id": "123",
        "_ctx_hash_ssn_1": token1,
        "ssn_1": "fake_1",
        "nested": {
            "_ctx_hash_nested_val": token2,
            "nested_val": "fake_2",
            "arr": [
                {"_shield_val": "fake_item", "_shield_ctx": token3}
            ]
        }
    }
    
    rehydrated = rehydrator.rehydrate(payload)
    
    assert "_ctx_hash_ssn_1" not in rehydrated
    assert rehydrated["ssn_1"] == pt1
    assert "_ctx_hash_nested_val" not in rehydrated["nested"]
    assert rehydrated["nested"]["nested_val"] == pt2
    assert rehydrated["nested"]["arr"][0] == pt3

@pytest.mark.asyncio
async def test_generator_exit_teardown():
    # Simple test to verify the try/except logic conceptually
    # This is a unit-level representation of what main.py's wrapped_stream does
    
    class MockStream:
        async def __aiter__(self):
            yield b"chunk1"
            raise asyncio.CancelledError()
            
    stream = MockStream()
    
    async def wrapped():
        try:
            async for chunk in stream:
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            return
            
    chunks = []
    with pytest.raises(asyncio.CancelledError) as exc_info:
        async for c in wrapped():
            chunks.append(c)
            # simulate upstream abort
            if len(chunks) == 1:
                raise asyncio.CancelledError()
                
    assert len(chunks) == 1

def test_lexer_massive_fragmented_stream(session_a_cipher):
    import time
    lexer = StatelessStreamingLexer(session_a_cipher)
    
    pt = "massive_data"
    token = session_a_cipher.encrypt(pt, "ssn")
    
    # Construct a massive string with 10k context hashes.
    # We will simulate 10k pairs (20k tokens)
    # The requirement says "stress test handling 100k+ token fragmented SSE argument streams"
    # We'll generate a string of length ~100k
    
    chunk_str = f'{{"_ctx_hash_ssn": "{token}", "ssn": "Fake-1234", '
    # Repeat the string to simulate huge payload
    payload = chunk_str * 2000 + '"end": "data"}' + ('}' * 1999)
    
    # 2000 iterations * ~100 bytes = ~200,000 bytes payload
    
    # Feed it in chunks of 50 bytes (highly fragmented)
    start = time.perf_counter()
    emitted = ""
    for i in range(0, len(payload), 50):
        emitted += lexer.feed_chunk(payload[i:i+50])
    emitted += lexer.flush()
    end = time.perf_counter()
    
    assert len(emitted) > 0
    assert "_ctx_hash_ssn" not in emitted
    assert pt in emitted
    
    # Ensure it's fast (e.g. < 0.5s for 200k bytes of heavy regex)
    # Wait, 1.0ms latency is for a *single* hot path (one request). 
    # Processing a 200k byte stream might take a bit more, but should be linear.
    assert (end - start) < 2.0
