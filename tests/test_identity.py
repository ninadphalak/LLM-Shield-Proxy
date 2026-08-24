import pytest
import jwt
from fastapi import HTTPException
from llm_shield_proxy.security.identity import verify_agent_identity
from llm_shield_proxy.core.config import request_policy_ctx, settings, agent_identity_ctx

class MockRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        class State:
            agent_identity_claim = None
        self.state = State()

@pytest.fixture(autouse=True)
def reset_config():
    settings.AGENT_IDENTITY_ENFORCER = True
    request_policy_ctx.set({})
    agent_identity_ctx.set(None)

@pytest.mark.asyncio
async def test_identity_enforcer_disabled_by_default():
    settings.AGENT_IDENTITY_ENFORCER = False
    req = MockRequest(headers={})
    await verify_agent_identity(req)
    assert req.state.agent_identity_claim is None
    assert agent_identity_ctx.get() is None

@pytest.mark.asyncio
async def test_missing_auth_header_fails():
    req = MockRequest(headers={"DPoP": "proof"})
    with pytest.raises(HTTPException) as exc:
        await verify_agent_identity(req)
    assert exc.value.status_code == 401
    assert "Missing or invalid Authorization header" in exc.value.detail

@pytest.mark.asyncio
async def test_missing_dpop_header_fails():
    req = MockRequest(headers={"Authorization": "Bearer token"})
    with pytest.raises(HTTPException) as exc:
        await verify_agent_identity(req)
    assert exc.value.status_code == 401
    assert "Missing DPoP proof" in exc.value.detail

@pytest.mark.asyncio
async def test_invalid_issuer_fails(monkeypatch):
    req = MockRequest(headers={"Authorization": "Bearer invalid_token", "DPoP": "my_dpop"})
    
    def mock_decode(token, *args, **kwargs):
        return {} # missing iss
        
    monkeypatch.setattr(jwt, "decode", mock_decode)
    monkeypatch.setattr(jwt, "get_unverified_header", lambda x: {})

    with pytest.raises(HTTPException) as exc:
        await verify_agent_identity(req)
    assert exc.value.status_code == 401
    assert "Invalid identity proof" in exc.value.detail

@pytest.mark.asyncio
async def test_successful_validation(monkeypatch):
    req = MockRequest(headers={"Authorization": "Bearer my_token", "DPoP": "my_dpop"})
    
    # Mock jwt.get_unverified_header
    monkeypatch.setattr(jwt, "get_unverified_header", lambda x: {"kid": "123"})
    
    # Mock jwt.decode
    def mock_decode(token, *args, **kwargs):
        if kwargs.get("options", {}).get("verify_signature") is False:
            if token == "my_token":
                return {"iss": "https://my-issuer.com"}
            elif token == "my_dpop":
                return {"htm": "POST", "htu": "https://my-api"}
        return {"sub": "agent_007"}
    
    monkeypatch.setattr(jwt, "decode", mock_decode)
    
    # Mock fetch_jwks
    class MockKey:
        key = "secret_key"
    class MockClient:
        def get_signing_key_from_jwt(self, token):
            return MockKey()
            
    monkeypatch.setattr("llm_shield_proxy.security.identity.fetch_jwks", lambda iss: MockClient())

    await verify_agent_identity(req)
    
    assert req.state.agent_identity_claim == "agent_007"
    assert agent_identity_ctx.get() == "agent_007"
