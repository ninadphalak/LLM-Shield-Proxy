import asyncio
import socket

import httpx
import pytest
import redis


def is_proxy_running():
    try:
        with socket.create_connection(("localhost", 8000), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not is_proxy_running(), reason="Proxy is not running on localhost:8000")
@pytest.mark.asyncio
async def test_sudden_client_disconnect():
    # Attempt to ping redis just to ensure we can connect, fail gracefully if not exposed
    try:
        r = redis.Redis(host="localhost", port=6379, db=0)
        r.ping()
        r.flushdb()
    except Exception:
        pass

    async def disconnected_request():
        timeout = httpx.Timeout(10.0)
        try:
            async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "Tell me a story"}],
                        "stream": True,
                    },
                ) as response:
                    async for chunk in response.aiter_bytes():
                        assert chunk
                        # Break out immediately after first chunk to simulate a broken pipe/disconnect
                        break
        except Exception:
            pass

    await disconnected_request()

    # Wait for the server to process the asyncio.CancelledError and cleanup
    await asyncio.sleep(1)

    # Verify the proxy didn't crash and is still healthy
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
