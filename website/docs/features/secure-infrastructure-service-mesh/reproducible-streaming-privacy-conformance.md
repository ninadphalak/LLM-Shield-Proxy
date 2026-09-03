# Reproducible Streaming Privacy Conformance

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The conformance harness empirically verifies the proxy's streaming privacy boundaries (e.g., handling split PII tokens across chunks, maintaining SSE framing) through a local benchmark without requiring a public LLM connection.

## How It Works
You can execute the harness to test the proxy's behavior under simulated load:
```bash
llm-shield-proxy benchmark --iterations 2000 --json-out CONFORMANCE_LATEST.json
```
The resulting JSON records the configuration, iteration count, validation evidence, and local timing distributions (p50/p95/p99). For privacy, it does not record the raw test values or the reconstructed payloads.

### Endpoint-Neutral Gateway Profile
The core conformance logic is available as a standalone package (`pii-leak-benchmark`) that does not depend on the proxy itself. It can test any OpenAI-compatible gateway via HTTP.

```bash
pip install pii-leak-benchmark
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name gateway-under-test \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
```

## Performance Profile
- **Overhead:** The benchmark evaluates local in-process microbenchmarks. **These do not reflect true end-to-end proxy latency.** They exclude ASGI middleware, HTTP parsing, TLS, network transit, and upstream model processing time.

## Implementation Details & Edge Cases
* **Formal Load Testing:** For production capacity planning, you must perform separate load testing. Use production-equivalent hardware, specify concurrency matrices, and measure total end-to-end latency (including network and upstream delays) rather than relying on the microbenchmark times.

## FAQ

**Q: Do the benchmark timing numbers guarantee my latency in production?**
A: No. They are isolated microbenchmarks of specific internal proxy components (like the sliding window buffer) to track regressions. They exclude major real-world latency sources like network I/O and upstream LLM generation time.

## Practical Effect
This conformance suite allows you to cryptographically and empirically verify that the proxy correctly handles complex streaming edge cases (like split PII tokens) before deploying it to production.

## Related Files
- `benchmarks/conformance.py`
- `.github/workflows/benchmark.yml`
