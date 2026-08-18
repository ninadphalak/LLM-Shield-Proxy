import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from llm_shield_proxy.api.main import app, app_state
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.security.rate_limit import rate_limiter
import asyncio

client = TestClient(app)

@pytest.fixture(autouse=True)
def reset_state():
    settings.OVERRIDE_CLIENT_AUTH = False
    settings.UPSTREAM_API_KEY = None
    settings.ENABLE_RATE_LIMITING = False
    settings.SHIELD_FAILURE_MODE = "FAIL_CLOSED"
    app_state.is_draining = False
    app_state.active_requests = 0
    yield

def test_enterprise_secret_injection():
    settings.OVERRIDE_CLIENT_AUTH = True
    settings.UPSTREAM_API_KEY = "sk-master-key"
    
    # We'll mock http_client.request to inspect the headers
    with patch("llm_shield_proxy.api.main.httpx.AsyncClient.request") as mock_request:
        # Return a mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b'{"success": true}'
        
        async def mock_req(*args, **kwargs):
            return mock_response
            
        mock_request.side_effect = mock_req
        
        # Send request with a virtual key
        response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer some-fake-client-key"}, json={})
        
        assert mock_request.called
        call_kwargs = mock_request.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        
        assert headers.get("authorization") == "Bearer sk-master-key"
        assert "x-api-key" not in headers

@pytest.mark.asyncio
async def test_rate_limiter_429():
    settings.ENABLE_RATE_LIMITING = True
    settings.RATE_LIMIT_RPM = 6000
    settings.RATE_LIMIT_BURST = 1
    
    # Force in-memory limiter to be used by mocking the Redis check
    with patch("llm_shield_proxy.security.rate_limit.vault_store") as mock_vault:
        mock_vault.redis = None
        
        # First request should pass
        assert await rate_limiter.acquire("test_vk") is True
        
        # Second request should fail immediately (burst is 1)
        # Note: in a real test we might need to manipulate time.monotonic, but a fast test works too
        assert await rate_limiter.acquire("test_vk") is False

def test_fail_closed_mode():
    settings.SHIELD_FAILURE_MODE = "FAIL_CLOSED"
    
    # Mock pii_engine.redact_payload to raise an exception
    with patch("llm_shield_proxy.api.main.pii_engine.redact_payload", side_effect=Exception("Simulated ONNX crash")):
        response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer BYOK"}, json={"test": "data"})
        
        assert response.status_code == 503
        assert "DLP Inspection Failure" in response.json()["error"]["message"]

def test_fail_open_mode():
    settings.SHIELD_FAILURE_MODE = "FAIL_OPEN"
    
    # Mock pii_engine.redact_payload to raise an exception, and mock httpx request
    with patch("llm_shield_proxy.api.main.pii_engine.redact_payload", side_effect=Exception("Simulated Redis drop")), \
         patch("llm_shield_proxy.api.main.httpx.AsyncClient.request") as mock_request:
         
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"success": True}
        
        async def mock_req(*args, **kwargs):
            return mock_response
            
        mock_request.side_effect = mock_req
        
        response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer BYOK"}, json={"test": "data"})
        
        # It should proxy the request despite the exception
        assert mock_request.called
        assert response.status_code == 200

def test_graceful_draining_503():
    app_state.is_draining = True
    
    response = client.get("/health")
    # Actually /health is an api_route but we have middleware that checks is_draining
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Service Unavailable: Pod Draining"
