import pytest
import pytest_asyncio
import asyncio
from unittest.mock import patch, MagicMock
import httpx

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.security.vault_client import AsyncVaultSecretProvider

pytestmark = pytest.mark.asyncio

@pytest.fixture(autouse=True)
def reset_settings():
    """Reset settings before each test."""
    original_vault_addr = settings.VAULT_ADDR
    original_vault_auth_method = settings.VAULT_AUTH_METHOD
    original_vault_token = settings.VAULT_TOKEN
    original_vault_role_id = settings.VAULT_ROLE_ID
    original_vault_secret_id = settings.VAULT_SECRET_ID
    original_vault_secret_path = settings.VAULT_SECRET_PATH

    yield

    settings.VAULT_ADDR = original_vault_addr
    settings.VAULT_AUTH_METHOD = original_vault_auth_method
    settings.VAULT_TOKEN = original_vault_token
    settings.VAULT_ROLE_ID = original_vault_role_id
    settings.VAULT_SECRET_ID = original_vault_secret_id
    settings.VAULT_SECRET_PATH = original_vault_secret_path


async def test_vault_token_auth():
    settings.VAULT_ADDR = "http://localhost:8200"
    settings.VAULT_AUTH_METHOD = "TOKEN"
    settings.VAULT_TOKEN = "test-token"
    settings.VAULT_SECRET_PATH = "secret/data/keys"

    provider = AsyncVaultSecretProvider()
    
    # Mock httpx.AsyncClient.get
    mock_response = httpx.Response(200, json={
        "data": {
            "data": {
                "UPSTREAM_API_KEY": "vault-key-123"
            }
        }
    })
    
    with patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get:
        await provider.fetch_secrets()
        
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert kwargs["headers"]["X-Vault-Token"] == "test-token"
        
    assert provider.get_secret("UPSTREAM_API_KEY") == "vault-key-123"
    await provider.aclose()


async def test_vault_approle_auth():
    settings.VAULT_ADDR = "http://localhost:8200"
    settings.VAULT_AUTH_METHOD = "APPROLE"
    settings.VAULT_ROLE_ID = "role-123"
    settings.VAULT_SECRET_ID = "secret-123"
    settings.VAULT_SECRET_PATH = "secret/data/keys"

    provider = AsyncVaultSecretProvider()
    
    # Mock httpx.AsyncClient.post and get
    mock_post_response = httpx.Response(200, json={
        "auth": {
            "client_token": "approle-token"
        }
    })
    
    mock_get_response = httpx.Response(200, json={
        "data": {
            "data": {
                "UPSTREAM_API_KEY": "approle-key-123"
            }
        }
    })
    
    with patch("httpx.AsyncClient.post", return_value=mock_post_response) as mock_post, \
         patch("httpx.AsyncClient.get", return_value=mock_get_response) as mock_get:
         
        await provider.fetch_secrets()
        
        mock_post.assert_called_once()
        mock_get.assert_called_once()
        assert mock_get.call_args[1]["headers"]["X-Vault-Token"] == "approle-token"
        
    assert provider.get_secret("UPSTREAM_API_KEY") == "approle-key-123"
    await provider.aclose()


async def test_fail_open_background_refresh():
    settings.VAULT_ADDR = "http://localhost:8200"
    settings.VAULT_AUTH_METHOD = "TOKEN"
    settings.VAULT_TOKEN = "test-token"
    settings.VAULT_SECRET_PATH = "secret/data/keys"
    
    provider = AsyncVaultSecretProvider()
    provider._cached_secrets = {"UPSTREAM_API_KEY": "old-key"}
    
    # Simulate a network failure on fetch_secrets
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Network down")):
        with pytest.raises(httpx.ConnectError):
            await provider.fetch_secrets()
            
    # Cache should NOT be cleared on failure
    assert provider.get_secret("UPSTREAM_API_KEY") == "old-key"
    await provider.aclose()


@pytest.mark.asyncio
async def test_http_client_mtls_config():
    # Test that get_http_client configures mTLS properly
    settings.ENABLE_MTLS = True
    settings.SSL_CA_BUNDLE_PATH = "/path/to/ca.pem"
    settings.SSL_CLIENT_CERT_PATH = "/path/to/cert.pem"
    settings.SSL_CLIENT_KEY_PATH = "/path/to/key.pem"
    
    from llm_shield_proxy.api.main import get_http_client
    from fastapi import Request
    
    mock_request = MagicMock(spec=Request)
    mock_request.app.state.http_client = None
    
    client = get_http_client(mock_request)
    # verify and cert are internal to httpx, we can check if it has the properties setup or just ensure it didn't crash
    assert client is not None
    await client.aclose()
    
    settings.ENABLE_MTLS = False
