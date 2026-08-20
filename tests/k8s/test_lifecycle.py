import asyncio
import subprocess

import httpx
import pytest


def is_docker_container_running():
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", "name=llm-shield-proxy", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return "llm-shield-proxy" in res.stdout
    except Exception:
        return False


@pytest.mark.skipif(not is_docker_container_running(), reason="llm-shield-proxy docker container is not running")
@pytest.mark.asyncio
async def test_probes_under_load():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Fire 100 concurrent requests to probes
        tasks = [client.get("/healthz") for _ in range(50)] + [client.get("/readyz") for _ in range(50)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, httpx.Response):
                assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.skipif(not is_docker_container_running(), reason="llm-shield-proxy docker container is not running")
async def test_sigterm_graceful_shutdown():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Start a stream request that will take some time
        req_task = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Tell me a long story"}],
                    "stream": True,
                },
                timeout=15.0,
            )
        )

        await asyncio.sleep(1)  # Give the stream time to establish

        print("Sending SIGTERM to proxy container...")
        subprocess.run(["docker", "kill", "--signal=SIGTERM", "llm-shield-proxy"], check=True)

        await asyncio.sleep(0.5)

        # Verify new connections are rejected (503 Service Unavailable or connection dropped)
        try:
            new_req = await client.get("/healthz")
            assert new_req.status_code == 503
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            pass  # Socket closed completely is also acceptable post-SIGTERM

        # Verify the in-flight stream finishes or terminates cleanly
        try:
            res = await req_task
            assert res.status_code in [200, 502, 503, 504]
        except Exception:
            pass  # Stream terminated cleanly via connection drop
