import asyncio
import statistics
import time
import tracemalloc

import orjson

from llm_shield_proxy.v3.ast_mutator import StatelessASTVisitor
from llm_shield_proxy.v3.crypto import StatelessPIICipher

# 55 MB Limit
MEMORY_LIMIT_BYTES = 55 * 1024 * 1024
# 1.0 ms Limit
LATENCY_LIMIT_SEC = 0.001

def create_nested_payload(depth: int) -> dict:
    payload = {"jsonrpc": "2.0", "method": "tools/call", "params": {"data": "secret_data_123"}}
    current = payload["params"]
    for i in range(depth - 1):
        current["nested"] = {"data": f"secret_data_{i}"}
        current = current["nested"]
    return payload

async def run_mutator_benchmark(mutator: StatelessASTVisitor, depth: int, iterations: int = 1000):
    payload = create_nested_payload(depth)
    raw_bytes = orjson.dumps(payload)

    latencies = []

    for _ in range(iterations):
        start = time.perf_counter()
        await mutator.mutate(raw_bytes)
        end = time.perf_counter()
        latencies.append(end - start)

    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=100)[94] if hasattr(statistics, "quantiles") else max(latencies) # Rough fallback
    p99 = statistics.quantiles(latencies, n=100)[98] if hasattr(statistics, "quantiles") else max(latencies)

    print(f"[AST Mutator - Depth {depth}] p50: {p50*1000:.3f}ms | p95: {p95*1000:.3f}ms | p99: {p99*1000:.3f}ms")
    assert p99 < LATENCY_LIMIT_SEC, f"p99 latency ({p99*1000:.3f}ms) exceeded 1.0ms limit for depth {depth}!"

async def run_crypto_benchmark(cipher: StatelessPIICipher, iterations: int = 1000):
    latencies = []
    pt = "highly_sensitive_data_1234567890"
    aad = "context_string"

    for _ in range(iterations):
        start = time.perf_counter()
        token = cipher.encrypt(pt, aad)
        decrypted = cipher.decrypt(token, aad)
        end = time.perf_counter()
        latencies.append(end - start)
        assert decrypted == pt

    p50 = statistics.median(latencies)
    p99 = statistics.quantiles(latencies, n=100)[98] if hasattr(statistics, "quantiles") else max(latencies)

    print(f"[Crypto AES-GCM] p50: {p50*1000:.3f}ms | p99: {p99*1000:.3f}ms")
    assert p99 < LATENCY_LIMIT_SEC, f"Crypto p99 latency ({p99*1000:.3f}ms) exceeded 1.0ms limit!"

async def memory_profiler(mutator: StatelessASTVisitor, iterations: int = 1000):
    print(f"\n[Memory Profiler] Starting {iterations} concurrent JSON-RPC requests...")
    tracemalloc.start()

    payload = create_nested_payload(20)
    raw_bytes = orjson.dumps(payload)

    # Fire all concurrently
    tasks = [mutator.mutate(raw_bytes) for _ in range(iterations)]
    await asyncio.gather(*tasks)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / (1024 * 1024)
    print(f"[Memory Profiler] Peak Memory Allocation: {peak_mb:.2f} MB")

    assert peak < MEMORY_LIMIT_BYTES, f"Peak memory {peak_mb:.2f} MB exceeded {MEMORY_LIMIT_BYTES / (1024*1024):.2f} MB limit!"
    print("[Memory Profiler] PASS: Memory is safely fenced under limits.\n")

async def main():
    print("=== v3 Engine Hardening & Benchmarks ===\n")
    cipher = StatelessPIICipher(keys={1: b"0" * 32}, version=1)
    mutator = StatelessASTVisitor(cipher)

    # 1. Latency Benchmarks
    await run_mutator_benchmark(mutator, 10)
    await run_mutator_benchmark(mutator, 20)
    await run_mutator_benchmark(mutator, 39)
    await run_crypto_benchmark(cipher)

    # 2. Memory Profiling
    await memory_profiler(mutator, iterations=1000)

    print("All benchmarks PASSED.")

if __name__ == "__main__":
    asyncio.run(main())
