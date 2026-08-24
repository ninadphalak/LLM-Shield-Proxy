from unittest.mock import patch

from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import vault_store

client = TestClient(app)


@patch("httpx.AsyncClient.request")
def test_ssrf_rejection(mock_request):
    # Test that overrides are rejected by default
    headers = {"X-Upstream-Base-Url": "http://169.254.169.254/latest/meta-data/", "x-api-key": "sk-proxy-test1"}

    # Enable override temporarily to test blacklist
    original_setting = settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE
    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = True

    response = client.post("/v1/chat/completions", headers=headers, json={})
    assert response.status_code == 403
    assert "Forbidden upstream hostname" in response.text

    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = original_setting


@patch("httpx.AsyncClient.request")
@patch("httpx.AsyncClient.send")
def test_vault_tenant_isolation(mock_send, mock_request):
    import httpx

    # Setup mock response so AuditLogger JSON serialization doesn't crash on AsyncMock
    mock_response = httpx.Response(
        status_code=200, content=b'{"choices":[]}', request=httpx.Request("POST", "http://test")
    )
    mock_request.return_value = mock_response

    # Inject valid virtual keys and mock upstream key for the test
    original_keys = settings.valid_virtual_keys_set
    original_openai_key = settings.OPENAI_API_KEY
    try:
        settings._valid_virtual_keys_set = {"sk-proxy-tenant-A", "sk-proxy-tenant-B"}
        settings.OPENAI_API_KEY = "mock_key"

        # Generate PII as Tenant A
        headers_a = {"X-Session-ID": "shared_sess_123", "x-api-key": "sk-proxy-tenant-A"}
        payload_a = {"model": "gpt-4", "messages": [{"role": "user", "content": "My email is test@example.com"}]}
        res_a = client.post("/v1/chat/completions", headers=headers_a, json=payload_a)
        assert res_a.status_code == 200

        # Verify Tenant A's vault has the email mapped
        from llm_shield_proxy.api.main import get_virtual_key_id
        hashed_key_a = get_virtual_key_id("sk-proxy-tenant-A")
        vault_a = vault_store.get_vault("shared_sess_123", hashed_key_a)
        assert "test@example.com" in vault_a.original_to_token
        token_id = vault_a.original_to_token["test@example.com"]

        # Query as Tenant B with the same session ID
        headers_b = {"X-Session-ID": "shared_sess_123", "x-api-key": "sk-proxy-tenant-B"}
        payload_b = {"model": "gpt-4", "messages": [{"role": "user", "content": "What is the name?"}]}
        res_b = client.post("/v1/chat/completions", headers=headers_b, json=payload_b)
        assert res_b.status_code == 200

        # Verify Tenant B gets a separate vault
        hashed_key_b = get_virtual_key_id("sk-proxy-tenant-B")
        vault_b = vault_store.get_vault("shared_sess_123", hashed_key_b)

        # Verify Tenant B's vault is isolated and does not contain the email
        assert "test@example.com" not in vault_b.original_to_token

        # Tenant B attempts to rehydrate Tenant A's token
        rehydrated = vault_b.rehydrate(token_id)
        # It should fail and return the token verbatim, NOT the email
        assert rehydrated == token_id
        assert "test@example.com" not in rehydrated
    finally:
        settings._valid_virtual_keys_set = original_keys
        settings.OPENAI_API_KEY = original_openai_key


def test_vault_tenant_isolation_unit():
    # Test VaultStore directly for namespace isolation
    vault_a = vault_store.get_vault("sess_999", "tenant_A")
    token_a = vault_a.get_or_create_token("secret@example.com", "EMAIL")

    vault_b = vault_store.get_vault("sess_999", "tenant_B")
    rehydrated = vault_b.rehydrate(token_a)
    assert rehydrated == token_a
    assert "secret@example.com" not in rehydrated
    assert "secret@example.com" not in vault_b.original_to_token


def test_body_size_limit_content_length():
    original_keys = settings.valid_virtual_keys_set
    settings._valid_virtual_keys_set = set()

    # Send an 11MB payload spoofed via header
    headers = {"x-api-key": "sk-proj-test", "Content-Length": str(11 * 1024 * 1024 + 1)}
    response = client.post("/v1/chat/completions", headers=headers, json={"data": "fake"})
    assert response.status_code == 413
    assert "payload exceeds maximum allowed limit" in response.text
    settings._valid_virtual_keys_set = original_keys
