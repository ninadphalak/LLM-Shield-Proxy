from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault, decrypt_from_token, encrypt_to_token
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer


@pytest.mark.asyncio
async def test_stateless_crypto_encryption_decryption():
    raw_pii = "test_user@example.com"
    token = encrypt_to_token(raw_pii)

    # Assert format
    assert token.startswith("[ENC_v1_")
    assert token.endswith("]")

    # Assert decryption
    decrypted = decrypt_from_token(token)
    assert decrypted == raw_pii

@pytest.mark.asyncio
async def test_stateless_crypto_invalid_decryption():
    # Invalid token (should return unmodified)
    invalid_token = "[ENC_v1_invalid_base64_payload]"
    decrypted = decrypt_from_token(invalid_token)
    assert decrypted == invalid_token

    # Not a token
    assert decrypt_from_token("just normal text") == "just normal text"

@pytest.mark.asyncio
async def test_split_token_rehydration_buffer():
    vault = StatelessCryptoVault()
    pii = "secret_password_123"
    token = vault.get_or_create_token(pii, "SECRET")

    buffer = SSERehydrationBuffer(vault)

    # Simulate receiving the token in 5 split chunks
    chunks = []
    chunk_size = max(1, len(token) // 5)
    for i in range(0, len(token), chunk_size):
        chunks.append(token[i:i+chunk_size])

    emitted = ""
    for i, chunk in enumerate(chunks):
        is_final = (i == len(chunks) - 1)
        res = buffer.process_delta_text(chunk, is_final=is_final)
        emitted += res

    # The buffer should rehydrate to the exact original string
    assert emitted == pii

@pytest.mark.asyncio
async def test_split_token_rehydration_buffer_with_surrounding_text():
    vault = StatelessCryptoVault()
    pii = "jane.doe@example.com"
    token = vault.get_or_create_token(pii, "EMAIL")

    buffer = SSERehydrationBuffer(vault)

    full_text = f"Here is the email: {token} and some other text."

    # Split into arbitrary chunks
    chunks = [
        full_text[:15],
        full_text[15:25], # middle of prefix
        full_text[25:35], # start of token
        full_text[35:45], # middle of token
        full_text[45:60], # end of token
        full_text[60:]
    ]

    emitted = ""
    for i, chunk in enumerate(chunks):
        is_final = (i == len(chunks) - 1)
        res = buffer.process_delta_text(chunk, is_final=is_final)
        emitted += res

    assert emitted == f"Here is the email: {pii} and some other text."

@pytest.mark.asyncio
async def test_zero_redis_calls_in_stateless_crypto_mode():
    from llm_shield_proxy.api.main import app

    # Mock vault_store.get_vault to raise an error if it's called
    with patch('llm_shield_proxy.api.main.vault_store.get_vault', side_effect=RuntimeError("Redis should not be called")):
        # Mock httpx.AsyncClient.request to return a dummy response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"response": "ok"}'
        mock_response.headers = {}
        mock_response.json.return_value = {"response": "ok"}

        with patch('httpx.AsyncClient.request', new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            import httpx
            transport = httpx.ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "Hello"}]},
                    headers={"X-Shield-Masking-Mode": "STATELESS_CRYPTO", "Authorization": "Bearer sk-proj-123"}
                )

                assert response.status_code == 200
                # If we get here without a RuntimeError, get_vault was not called
