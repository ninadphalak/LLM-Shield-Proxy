from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)

@pytest.fixture
def mock_httpx_send():
    with patch("httpx.AsyncClient.send", new_callable=AsyncMock) as mock_send:
        yield mock_send

@pytest.fixture
def mock_httpx_request():
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        yield mock_request


def test_transient_recovery(monkeypatch, mock_httpx_request):
    """Test 1: Transient Recovery - Mock 429 then 200 OK. Validate retry jitter."""
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "MAX_RETRIES", 3)

    # First call fails with 429, second succeeds with 200
    mock_response_429 = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    mock_response_200 = httpx.Response(200, json={"id": "chatcmpl-test", "choices": [{"message": {"role": "assistant", "content": "Success"}}]}, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))

    mock_httpx_request.side_effect = [
        httpx.HTTPStatusError("Rate Limit", request=mock_response_429.request, response=mock_response_429),
        mock_response_200
    ]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-proj-mock-key"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]},
        )

        assert response.status_code == 200
        assert mock_httpx_request.call_count == 2
        mock_sleep.assert_called_once()
        # Verify the sleep time was passed
        sleep_arg = mock_sleep.call_args[0][0]
        assert 0.25 <= sleep_arg <= 5.0 # Jitter formula: min(5.0, 0.5 * 2**0) * uniform(0.5, 1.0) -> max 0.5 * 1.0 = 0.5


def test_explicit_failover(monkeypatch, mock_httpx_request):
    """Test 2: Explicit Failover - Mock 503 persistently. Pass X-Shield-Fallback-URL and validate."""
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "MAX_RETRIES", 1) # Reduce retries for faster test

    # 503 response
    mock_response_503 = httpx.Response(503, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    # Fallback success response
    mock_response_200 = httpx.Response(200, json={"id": "chatcmpl-fallback", "choices": [{"message": {"role": "assistant", "content": "Fallback Success"}}]}, request=httpx.Request("POST", "https://fallback.example.com/v1/chat/completions"))

    mock_httpx_request.side_effect = [
        httpx.HTTPStatusError("Service Unavailable", request=mock_response_503.request, response=mock_response_503), # attempt 0
        httpx.HTTPStatusError("Service Unavailable", request=mock_response_503.request, response=mock_response_503), # attempt 1
        mock_response_200 # fallback attempt
    ]

    fallback_url = "https://fallback.example.com"

    with patch("asyncio.sleep", new_callable=AsyncMock):
        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-proj-mock-key",
                "X-Shield-Fallback-URL": fallback_url
            },
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]},
        )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Fallback Success"
        assert mock_httpx_request.call_count == 3

        # Verify the last call was to the fallback URL
        last_call_url = str(mock_httpx_request.call_args_list[-1].kwargs["url"])
        from urllib.parse import urlparse
        assert urlparse(last_call_url).hostname == "fallback.example.com"


def test_fast_fail(monkeypatch, mock_httpx_request):
    """Test 3: Fast Fail - Mock 400 Bad Request. Validate immediate failure without retries."""
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "MAX_RETRIES", 3)

    mock_response_400 = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    mock_httpx_request.side_effect = httpx.HTTPStatusError("Bad Request", request=mock_response_400.request, response=mock_response_400)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-proj-mock-key"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]},
        )

        assert response.status_code == 400
        assert mock_httpx_request.call_count == 1
        mock_sleep.assert_not_called()
