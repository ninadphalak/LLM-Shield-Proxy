import pytest
from httpx import AsyncClient, ASGITransport

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.security.circuit_breaker import circuit_breaker_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the circuit breaker cache before each test."""
    circuit_breaker_cache.clear()
    settings.AGENT_BREAKER_THRESHOLD = 3
    settings.ENABLE_AGENT_BREAKER = True


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_consecutive_duplicates():
    """Simulate 3 consecutive identical tool-call requests and assert HTTP 429."""
    
    session_id = "test-session-123"
    
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_db",
                            "arguments": '{"query": "error"}'
                        }
                    }
                ]
            }
        ]
    }
    
    headers = {
        "X-Session-ID": session_id,
        "Authorization": "Bearer sk-proj-123",
        "X-Upstream-Base-Url": "https://api.openai.com"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Request 1: OK
        response1 = await ac.post("/v1/chat/completions", json=payload, headers=headers)
        assert response1.status_code != 429
        
        # Request 2: OK
        response2 = await ac.post("/v1/chat/completions", json=payload, headers=headers)
        assert response2.status_code != 429

        # Request 3: Should trip
        response3 = await ac.post("/v1/chat/completions", json=payload, headers=headers)
        
        assert response3.status_code == 429
        data = response3.json()
        assert data["error"] == "circuit_breaker_tripped"
        assert data["reason"] == "agent_loop_detected"
        assert data["consecutive_turns"] == 3
        
        assert response3.headers.get("X-Shield-Circuit-Breaker") == "TRIPPED"
        assert response3.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_circuit_breaker_remains_closed_for_diverse_requests():
    """Simulate 5 diverse requests and assert circuit breaker remains closed."""
    session_id = "test-session-456"
    
    headers = {
        "X-Session-ID": session_id,
        "Authorization": "Bearer sk-proj-123",
        "X-Upstream-Base-Url": "https://api.openai.com"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for i in range(5):
            payload = {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "search_db",
                                    "arguments": f'{{"query": "query_{i}"}}'
                                }
                            }
                        ]
                    }
                ]
            }
            
            response = await ac.post("/v1/chat/completions", json=payload, headers=headers)
            assert response.status_code != 429


@pytest.mark.asyncio
async def test_circuit_breaker_bypass_header():
    """Assert X-Shield-Bypass-Breaker: true allows duplicate requests."""
    session_id = "test-session-789"
    
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": "Tell me a joke."
            }
        ]
    }
    
    headers = {
        "X-Session-ID": session_id,
        "Authorization": "Bearer sk-proj-123",
        "X-Upstream-Base-Url": "https://api.openai.com",
        "X-Shield-Bypass-Breaker": "true"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Send 4 identical requests, should not trip because of bypass
        for _ in range(4):
            response = await ac.post("/v1/chat/completions", json=payload, headers=headers)
            assert response.status_code != 429


@pytest.mark.asyncio
async def test_circuit_breaker_pagination_trap():
    """Assert pagination (e.g. OFFSET 100 to OFFSET 200) does not trip breaker."""
    session_id = "test-session-pagination"
    headers = {
        "X-Session-ID": session_id,
        "Authorization": "Bearer sk-proj-123",
        "X-Upstream-Base-Url": "https://api.openai.com"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for offset in [100, 200, 300, 400, 500]:
            # The payload needs to be > 50 chars to avoid the short ping trap
            payload = {
                "model": "gpt-4",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Please execute the following query: SELECT * FROM users ORDER BY id DESC LIMIT 10 OFFSET {offset}"
                    }
                ]
            }
            response = await ac.post("/v1/chat/completions", json=payload, headers=headers)
            assert response.status_code != 429


