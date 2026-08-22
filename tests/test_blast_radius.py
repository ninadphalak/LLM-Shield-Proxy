import time
from unittest.mock import AsyncMock, patch

import pytest
import redis.exceptions
from httpx import ASGITransport, AsyncClient, MockTransport, Response

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings


@pytest.fixture
def override_settings():
    original_enable = settings.ENABLE_BLAST_RADIUS_LIMITS
    original_burst = settings.BLAST_RADIUS_BURST_CAPACITY
    original_replenish = settings.BLAST_RADIUS_REPLENISH_RATE_PER_MIN
    original_keys = settings.valid_virtual_keys_set

    settings.ENABLE_BLAST_RADIUS_LIMITS = True
    settings.BLAST_RADIUS_BURST_CAPACITY = 100
    settings.BLAST_RADIUS_REPLENISH_RATE_PER_MIN = 600  # 10 tokens per second
    settings.valid_virtual_keys_set = frozenset(["test_key"])

    yield

    settings.ENABLE_BLAST_RADIUS_LIMITS = original_enable
    settings.BLAST_RADIUS_BURST_CAPACITY = original_burst
    settings.BLAST_RADIUS_REPLENISH_RATE_PER_MIN = original_replenish
    settings.valid_virtual_keys_set = original_keys

@pytest.mark.asyncio
async def test_burst_capacity_exceeded(override_settings):
    # Burst is 100. Send a payload with 105 entities (emails)
    emails = " ".join([f"test{i}@example.com" for i in range(105)])
    payload = {"messages": [{"role": "user", "content": emails}]}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer test_key"}
        )

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "blast_radius_exceeded"

@pytest.mark.asyncio
async def test_replenish_rate(override_settings):
    # Send 60 entities. Should pass.
    emails_60 = " ".join([f"test{i}@example.com" for i in range(60)])
    payload_60 = {"messages": [{"role": "user", "content": emails_60}]}

    def handle_request(request):
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=request)

    with patch("llm_shield_proxy.api.main.get_http_client") as mock_get_client:
        mock_get_client.return_value = AsyncClient(transport=MockTransport(handle_request))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response1 = await client.post(
                "/v1/chat/completions",
                json=payload_60,
                headers={"Authorization": "Bearer test_key"}
            )
            assert response1.status_code == 200

            # Send 50 more entities immediately. Total = 110 > 100. Should fail.
            emails_50 = " ".join([f"test{i}@example.com" for i in range(50)])
            payload_50 = {"messages": [{"role": "user", "content": emails_50}]}

            response2 = await client.post(
                "/v1/chat/completions",
                json=payload_50,
                headers={"Authorization": "Bearer test_key"}
            )
            assert response2.status_code == 429

            # Fast-forward time to replenish bucket (mock time.monotonic for InMemoryBucket)
            with patch("time.monotonic", return_value=time.monotonic() + 10.0):
                # 10 seconds * 10 tokens/sec = 100 tokens replenished
                response3 = await client.post(
                    "/v1/chat/completions",
                    json=payload_50,
                    headers={"Authorization": "Bearer test_key"}
                )
                assert response3.status_code == 200

@pytest.mark.asyncio
async def test_fail_open_redis_failure(override_settings):
    # Exceed burst capacity
    emails = " ".join([f"test{i}@example.com" for i in range(150)])
    payload = {"messages": [{"role": "user", "content": emails}]}

    def handle_request(request):
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=request)

    with patch("llm_shield_proxy.api.main.get_http_client") as mock_get_client:
        mock_get_client.return_value = AsyncClient(transport=MockTransport(handle_request))
        with patch("llm_shield_proxy.security.rate_limit.vault_store") as mock_vs:
            from llm_shield_proxy.engines.vault import RedisVaultStore
            mock_vs.__class__ = RedisVaultStore
            mock_vs.async_client = AsyncMock()
            mock_vs.async_client.evalsha.side_effect = redis.exceptions.RedisError("Cluster offline")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": "Bearer test_key"}
                )

    # Should fail open and process the request (200 OK)
    assert response.status_code == 200
