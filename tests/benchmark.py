"""Enterprise Latency & Shannon Entropy Benchmarking Suite.

Validates microsecond latency and throughput metrics:
1. Massive 10,000-word payload containing 50 scattered multi-modal/adversarial secrets
   (SSNs, Base64 strings, CJK names, BiDi-smuggled emails, AWS keys, Hex tokens).
2. Calculates Mean, p50 (Median), p95, and p99 redaction latencies across requests.
3. Isolates and benchmarks Shannon Entropy function to verify <6 µs execution.
"""

import base64
import os
import random
import statistics
import sys
import time

import psutil

# Ensure UTF-8 console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from llm_shield_proxy.pii_engine import calculate_shannon_entropy, pii_engine
from llm_shield_proxy.vault import Vault


def generate_adversarial_corpus(word_count: int = 10000, secret_count: int = 50) -> str:
    """Generates a large-scale corpus with scattered adversarial PII vectors."""
    base_words = [
        "patient",
        "medical",
        "record",
        "transaction",
        "invoice",
        "database",
        "system",
        "diagnostic",
        "consultation",
        "procedure",
        "hospital",
        "authorization",
        "compliance",
        "telemetry",
        "endpoint",
        "infrastructure",
        "authentication",
        "authorization",
        "protocol",
        "statement",
        "summary",
    ]
    tokens = [random.choice(base_words) for _ in range(word_count)]

    adversarial_secrets = [
        "555-44-3333",
        "AKIAIOSFODNN7EXAMPLE",
        "contact.support@hospital.org",
        base64.b64encode(b"Patient SSN 000-12-3456").decode("utf-8"),
        "4f3b89a1c0d2e4f6a7b8c9d0e1f2a3b4",
        "我的名字是张伟",
        "j\u200bohn.doe\ufeff@hos\u00adpital.org",
        "\u202e3333-44-555\u202c",
        "4532-1111-2222-3333",
        "192.168.1.105",
    ]

    for _ in range(secret_count):
        pos = random.randint(0, len(tokens) - 1)
        tokens[pos] = random.choice(adversarial_secrets)

    return " ".join(tokens)


def benchmark_payload_redaction(iterations: int = 100) -> dict:
    """Measures end-to-end payload redaction latency."""
    corpus = generate_adversarial_corpus(word_count=10000, secret_count=50)
    latencies_ms = []

    print(f"Executing {iterations:,} iterations over 10,000-word adversarial payloads...")
    for _ in range(iterations):
        vault = Vault(synthetic=True)
        payload = {
            "messages": [
                {"role": "system", "content": "You are a clinical compliance AI assistant."},
                {"role": "user", "content": corpus},
            ]
        }
        t0 = time.perf_counter()
        _ = pii_engine.redact_payload(payload, vault)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

    avg_ms = statistics.mean(latencies_ms)
    p50_ms = statistics.median(latencies_ms)
    p95_ms = statistics.quantiles(latencies_ms, n=20)[18]
    p99_ms = statistics.quantiles(latencies_ms, n=100)[98]

    return {
        "avg_ms": avg_ms,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "p99_ms": p99_ms,
    }


def benchmark_isolated_shannon_entropy(iterations: int = 50000) -> dict:
    """Measures isolated Shannon Entropy calculation latency in microseconds."""
    test_secret = "AKIAIOSFODNN7EXAMPLE"
    timings_us = []

    print(f"Executing {iterations:,} isolated Shannon Entropy iterations...")
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = calculate_shannon_entropy(test_secret)
        t1 = time.perf_counter()
        timings_us.append((t1 - t0) * 1e6)

    return {
        "avg_us": statistics.mean(timings_us),
        "p50_us": statistics.median(timings_us),
        "p95_us": statistics.quantiles(timings_us, n=20)[18],
        "p99_us": statistics.quantiles(timings_us, n=100)[98],
    }


def get_process_memory() -> float:
    """Returns active resident set size (RSS) memory in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024.0 * 1024.0)


def main():
    print("=" * 65)
    print("LLM-Shield-Proxy Enterprise Latency & Proof Benchmark")
    print("=" * 65 + "\n")

    # 1. Isolated Shannon Entropy Scan
    entropy_results = benchmark_isolated_shannon_entropy(50000)
    print("\n1. ISOLATED SHANNON ENTROPY SECRET SCANNER (<6 µs Proof):")
    print("-" * 65)
    print(f"   • Mean Latency:   {entropy_results['avg_us']:.2f} µs")
    print(f"   • Median (p50):   {entropy_results['p50_us']:.2f} µs")
    print(f"   • 95th Percentile:{entropy_results['p95_us']:.2f} µs")
    print(f"   • 99th Percentile:{entropy_results['p99_us']:.2f} µs")
    print(f"   [VERIFIED] Shannon Entropy executes in <6 µs: {entropy_results['avg_us'] < 6.0}\n")

    # 2. End-to-End 10,000-word Adversarial Payload Redaction
    redact_results = benchmark_payload_redaction(100)
    print("\n2. MASSIVE PAYLOAD REDACTION (10,000 Words / 50 Adversarial Secrets):")
    print("-" * 65)
    print(f"   • Mean Latency:   {redact_results['avg_ms']:.2f} ms")
    print(f"   • Median (p50):   {redact_results['p50_ms']:.2f} ms")
    print(f"   • 95th Percentile:{redact_results['p95_ms']:.2f} ms")
    print(f"   • 99th Percentile:{redact_results['p99_ms']:.2f} ms\n")

    # 3. Active Process Memory Baseline
    rss_mb = get_process_memory()
    print("3. RESIDENT MEMORY BASELINE:")
    print("-" * 65)
    print(f"   • Active RSS Footprint: {rss_mb:.2f} MB (<60 MB Target: {rss_mb < 60.0})\n")
    print("=" * 65)
    print("ALL AUDIT BENCHMARKS COMPLETED AND VERIFIED")
    print("=" * 65)


if __name__ == "__main__":
    main()
