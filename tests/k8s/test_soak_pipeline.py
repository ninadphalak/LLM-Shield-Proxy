"""Endurance/soak coverage for the real redaction + SSE streaming pipeline.

The existing k8s harness test (test_concurrency_cgroup.py) only hammers /healthz,
which never touches PII detection, vault insertion, or SSE rehydration -- so it
can't catch a leak in any of those subsystems. This file closes that gap with
two tests:

1. test_soak_streaming_pipeline_memory_stability: sends sustained, PII-bearing
   traffic (mixed streaming/non-streaming, rotating + reused session IDs) at the
   live proxy container and asserts RSS both stays under a hard ceiling AND does
   not show a runaway upward trend over the run -- a single peak-under-threshold
   check (as in test_concurrency_cgroup.py) would miss a slow leak that hasn't
   yet crossed the ceiling by the time the test ends.

2. test_audit_queue_drops_under_backpressure_without_blocking_caller: a fast,
   in-process regression test for the bounded-queue/drop-policy fix in
   observability/audit.py. Stalls the WORM worker thread deterministically (by
   holding its chain lock, simulating a stalled log sink) and floods the queue
   past its configured maxsize, asserting memory stays bounded and the caller is
   never blocked or raised into.

Duration is intentionally short by default so this runs in normal CI. Set
SOAK_DURATION_SECONDS (and optionally SOAK_CONCURRENCY) to run a real multi-hour
soak, e.g.:

    SOAK_DURATION_SECONDS=25200 SOAK_CONCURRENCY=50 \\
        py -m pytest tests/k8s/test_soak_pipeline.py -s -v --noconftest
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import subprocess
import time

import httpx
import pytest

PROXY_CONTAINER_NAME = "llm-shield-proxy"

SOAK_DURATION_SECONDS = float(os.environ.get("SOAK_DURATION_SECONDS", "60"))
SOAK_CONCURRENCY = int(os.environ.get("SOAK_CONCURRENCY", "10"))
MEMORY_SAMPLE_INTERVAL_SECONDS = 1.0
MEMORY_WARMUP_SAMPLES = 3  # skip early samples (connection pool / model warmup)
HARD_MEMORY_CEILING_MB = 150.0

PII_MESSAGE_TEMPLATES = [
    "Hi, my name is {name} and my email is {email}, please update my file.",
    "Contact {name} at {phone} regarding invoice for card {cc}.",
    "SSN on file for {name} is {ssn}, please confirm before proceeding.",
    "Just chatting about the weather today, nothing sensitive here at all.",
    "Following up: {name} ({email}) called about their account, phone {phone}.",
]

_NAMES = ["Sarah Connor", "John Smith", "Maria Garcia", "Wei Chen", "Amara Okafor"]
_EMAILS = ["sarah@example.com", "john.smith@corp.io", "maria.g@health.org"]
_PHONES = ["555-0199", "555-0142", "555-0110"]
_CCS = ["4111-1111-1111-1111", "5500-0000-0000-0004"]
_SSNS = ["078-05-1120", "219-09-9999"]


def is_docker_container_running() -> bool:
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", f"name={PROXY_CONTAINER_NAME}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return PROXY_CONTAINER_NAME in res.stdout
    except Exception:
        return False


async def _fetch_memory_mb() -> float:
    try:
        cmd = ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", PROXY_CONTAINER_NAME]
        output = subprocess.check_output(cmd).decode("utf-8").strip()
        if not output:
            return 0.0
        mem_str = output.split(" / ")[0].strip()
        if "MiB" in mem_str:
            return float(mem_str.replace("MiB", ""))
        if "KiB" in mem_str:
            return float(mem_str.replace("KiB", "")) / 1024.0
        if "GiB" in mem_str:
            return float(mem_str.replace("GiB", "")) * 1024.0
        if "B" in mem_str:
            return float(mem_str.replace("B", "")) / (1024.0 * 1024.0)
        return 0.0
    except Exception:
        return 0.0


def _build_payload(counter: int) -> dict:
    template = PII_MESSAGE_TEMPLATES[counter % len(PII_MESSAGE_TEMPLATES)]
    content = template.format(
        name=_NAMES[counter % len(_NAMES)],
        email=_EMAILS[counter % len(_EMAILS)],
        phone=_PHONES[counter % len(_PHONES)],
        cc=_CCS[counter % len(_CCS)],
        ssn=_SSNS[counter % len(_SSNS)],
    )
    return {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": content}],
        "stream": counter % 2 == 0,
    }


@pytest.mark.skipif(not is_docker_container_running(), reason="llm-shield-proxy docker container is not running")
@pytest.mark.asyncio
async def test_soak_streaming_pipeline_memory_stability():
    """Sustained PII redaction + SSE rehydration traffic must not leak memory.

    Rotates through a pool of session IDs (some reused across requests to
    exercise vault lookup/reuse, some fresh to exercise vault creation and
    eventual TTL/LRU eviction) while mixing streaming and non-streaming
    requests, all carrying realistic PII so the full inbound-redaction and
    SSE-rehydration pipeline is actually exercised end to end.
    """
    session_pool = [f"soak-session-{i}" for i in range(25)]
    counter = itertools.count()
    memory_samples: list[float] = []
    stop = False
    errors: list[str] = []

    async def monitor_memory():
        while not stop:
            memory_samples.append(await _fetch_memory_mb())
            await asyncio.sleep(MEMORY_SAMPLE_INTERVAL_SECONDS)

    async def worker(client: httpx.AsyncClient):
        nonlocal errors
        while not stop:
            n = next(counter)
            session_id = session_pool[n % len(session_pool)]
            payload = _build_payload(n)
            try:
                if payload["stream"]:
                    async with client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=payload,
                        headers={"X-Session-ID": session_id},
                        timeout=15.0,
                    ) as resp:
                        async for _chunk in resp.aiter_bytes():
                            pass
                        if resp.status_code >= 500:
                            errors.append(f"stream status {resp.status_code}")
                else:
                    resp = await client.post(
                        "/v1/chat/completions",
                        json=payload,
                        headers={"X-Session-ID": session_id},
                        timeout=15.0,
                    )
                    if resp.status_code >= 500:
                        errors.append(f"json status {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        monitor_task = asyncio.create_task(monitor_memory())
        workers = [asyncio.create_task(worker(client)) for _ in range(SOAK_CONCURRENCY)]

        await asyncio.sleep(SOAK_DURATION_SECONDS)

        stop = True
        await asyncio.gather(*workers, return_exceptions=True)
        await monitor_task

    print(f"Soak test issued requests, sampled {len(memory_samples)} memory points.")
    print(f"Memory samples (MB): {memory_samples}")
    if errors:
        print(f"Observed {len(errors)} request-level errors (first 5): {errors[:5]}")

    # 5xx/connection errors are tolerated sparingly (transient), but the proxy
    # must not be falling over under sustained day-to-day traffic.
    assert len(errors) < max(5, 0.05 * next(counter)), "Excessive request failures during soak run"

    usable_samples = memory_samples[MEMORY_WARMUP_SAMPLES:]
    assert usable_samples, "No memory samples collected -- is `docker stats` reachable?"

    peak_mb = max(usable_samples)
    assert peak_mb < HARD_MEMORY_CEILING_MB, f"Peak RSS {peak_mb}MB exceeded hard ceiling {HARD_MEMORY_CEILING_MB}MB"

    # Leak trend check: compare the mean of the first third of the run (post
    # warmup) against the mean of the last third. Some growth is expected
    # (connection pools ramping up, LRU caches filling toward their cap), but a
    # real leak shows up as an unbounded upward trend rather than a plateau.
    third = max(1, len(usable_samples) // 3)
    early_avg = sum(usable_samples[:third]) / third
    late_avg = sum(usable_samples[-third:]) / third
    growth_ratio = late_avg / early_avg if early_avg > 0 else 1.0

    print(f"Early-window avg RSS: {early_avg:.1f}MB, late-window avg RSS: {late_avg:.1f}MB, ratio: {growth_ratio:.2f}")
    assert late_avg <= early_avg * 1.5 + 20.0, (
        f"Memory grew from {early_avg:.1f}MB to {late_avg:.1f}MB over the soak run "
        "-- looks like a leak, not warmup/cache fill."
    )


def test_audit_queue_drops_under_backpressure_without_blocking_caller(caplog):
    """Regression test for the unbounded audit-queue memory leak fix.

    Stalls the WORM worker thread deterministically by holding its chain lock
    (standing in for a stalled log collector / slow stdout sink -- a routine
    ops condition, not an edge case) and floods `_enqueue_log` well past the
    bounded queue's maxsize. Asserts the queue never exceeds that bound, the
    caller is never raised into or blocked, and a WARNING is emitted once
    events start being dropped.
    """
    from llm_shield_proxy.observability.audit import AuditLogger

    AuditLogger._start_worker_if_needed()

    # Drain anything left over from earlier tests before we start.
    deadline = time.time() + 5.0
    while not AuditLogger._log_queue.empty() and time.time() < deadline:
        time.sleep(0.01)

    maxsize = AuditLogger._log_queue.maxsize
    assert maxsize > 0, "audit queue must be bounded (maxsize > 0), not unlimited"

    acquired = AuditLogger._chain_lock.acquire(timeout=5.0)
    assert acquired, "could not acquire chain lock to stall the worker for this test"
    try:
        # One event to get the worker to dequeue-then-block on the held lock.
        AuditLogger.log_security_event("soak_test_probe", "INFO", {"n": -1})
        time.sleep(0.2)

        observed_full = False
        with caplog.at_level(logging.WARNING, logger="llm_shield_proxy.observability.audit"):
            for i in range(maxsize + 500):
                # Must never raise or block, no matter how far past capacity we push.
                AuditLogger.log_security_event("soak_test_flood", "INFO", {"n": i})
                qsize = AuditLogger._log_queue.qsize()
                assert qsize <= maxsize, f"audit queue grew past its bound: {qsize} > {maxsize}"
                if qsize >= maxsize:
                    observed_full = True

        assert observed_full, "expected the queue to reach capacity under sustained backpressure"
        assert any(
            "queue full" in rec.getMessage().lower() for rec in caplog.records
        ), "expected a WARNING log when the audit queue starts dropping events"
    finally:
        AuditLogger._chain_lock.release()

    # Let the worker drain now that the simulated stall is over, so later tests
    # in the same process start from a clean queue.
    deadline = time.time() + 5.0
    while not AuditLogger._log_queue.empty() and time.time() < deadline:
        time.sleep(0.01)
