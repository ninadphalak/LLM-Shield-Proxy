import asyncio
import subprocess

import httpx
import pytest


async def fetch_memory_usage():
    try:
        cmd = ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "llm-shield-proxy"]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        if not output:
            return 0.0

        mem_str = output.split(" / ")[0].strip()
        mem_mb = 0.0
        if "MiB" in mem_str:
            mem_mb = float(mem_str.replace("MiB", ""))
        elif "KiB" in mem_str:
            mem_mb = float(mem_str.replace("KiB", "")) / 1024.0
        elif "GiB" in mem_str:
            mem_mb = float(mem_str.replace("GiB", "")) * 1024.0
        elif "B" in mem_str:
            mem_mb = float(mem_str.replace("B", "")) / (1024.0 * 1024.0)
        return mem_mb
    except Exception:
        return 0.0

@pytest.mark.asyncio
async def test_concurrency_memory_limits():
    concurrency = 500
    total_requests = 10000

    semaphore = asyncio.Semaphore(concurrency)
    max_memory_observed = 0.0

    async def make_request(client):
        async with semaphore:
            try:
                res = await client.get("/healthz")
                return res.status_code
            except Exception:
                return 500

    limits = httpx.Limits(max_connections=500, max_keepalive_connections=500)
    async with httpx.AsyncClient(base_url="http://localhost:8000", limits=limits, timeout=30.0) as client:
        monitoring = True

        async def monitor_mem():
            nonlocal max_memory_observed
            while monitoring:
                mem = await fetch_memory_usage()
                if mem > max_memory_observed:
                    max_memory_observed = mem
                await asyncio.sleep(0.5)

        monitor_task = asyncio.create_task(monitor_mem())

        tasks = [make_request(client) for _ in range(total_requests)]
        await asyncio.gather(*tasks)

        monitoring = False
        await monitor_task

    print(f"Peak memory observed: {max_memory_observed} MB")

    # Assert peak memory consumption strictly stays below 55MB RAM
    assert max_memory_observed < 55.0, f"OOM Limit Exceeded: {max_memory_observed}MB >= 55MB"
