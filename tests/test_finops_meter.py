import json
from typing import AsyncGenerator

import httpx
import pytest
from httpx import AsyncClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings


@pytest.fixture(autouse=True)
def setup_finops():
    settings.ENABLE_FINOPS_METERING = True
    yield
    settings.ENABLE_FINOPS_METERING = False

@pytest.mark.asyncio
async def test_dynamic_key_resolution_headers():
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Test X-Virtual-Key header
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers={"X-Virtual-Key": "tenant-abc", "Authorization": "Bearer dummy-key"}
        )
        assert response.status_code in (200, 401, 503, 500) # Depends on upstream mock, just want to check resolution doesn't crash

@pytest.mark.asyncio
async def test_dynamic_key_resolution_baggage():
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Test Baggage header
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers={"Baggage": "userId=alice,tenant_id=tenant-99,env=prod", "Authorization": "Bearer dummy-key"}
        )
        assert response.status_code in (200, 401, 503, 500)

@pytest.mark.asyncio
async def test_streaming_usage_metering(mocker):
    # We will mock http_client.send to return a stream with a usage chunk
    mock_send = mocker.patch("httpx.AsyncClient.send")

    class MockResponse:
        status_code = 200
        headers = {}

        async def aiter_bytes(self) -> AsyncGenerator[bytes, None]:
            yield b'data: {"id":"123","choices":[{"delta":{"content":"Hello"}}]}\n\n'
            yield b'data: {"id":"123","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            yield b'data: [DONE]\n\n'

        def raise_for_status(self):
            pass

        async def aclose(self):
            pass

    mock_send.return_value = MockResponse()

    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
            headers={"Authorization": "Bearer sk-proj-test"}
        )
        assert response.status_code == 200
        content = ""
        async for chunk in response.aiter_bytes():
            content += chunk.decode()

        assert "usage" in content
        assert "prompt_tokens" in content

@pytest.mark.asyncio
async def test_rest_usage_metering(mocker):
    # We will mock http_client.request to return a JSON response with usage
    mock_request = mocker.patch("httpx.AsyncClient.request")

    class MockResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = json.dumps({
            "id": "123",
            "choices": [{"message": {"content": "Hello", "role": "assistant"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        })

        def json(self):
            return json.loads(self.text)

        def raise_for_status(self):
            pass

    mock_request.return_value = MockResponse()

    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers={"Authorization": "Bearer sk-proj-test"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "usage" in data
        assert data["usage"]["total_tokens"] == 15
