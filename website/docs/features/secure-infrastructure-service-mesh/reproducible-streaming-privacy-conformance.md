# Reproducible Streaming Privacy Conformance

[Back to Features Catalog](/docs/features-overview)

The conformance harness checks the proxy's distinctive streaming safety properties without contacting a public LLM:

- every two-part split and the character-by-character split of a protected placeholder
- SSE reconstruction and preservation of the `[DONE]` marker
- the pre-upstream privacy boundary for representative entity classes
- bounded in-process measurements for the no-op and sliding-window paths

```bash
llm-shield-proxy benchmark --iterations 2000 --json-out CONFORMANCE_LATEST.json
```

The JSON report records schema version, source revision when available, runtime/platform details, iteration count, pass/fail evidence, allocation observations, and p50/p95/p99 timing distributions. It deliberately excludes the actual test PII and reconstructed placeholder values.

## What the timing numbers mean

These are local in-process microbenchmarks. They exclude ASGI middleware, HTTP parsing, TLS, network latency, upstream model time, concurrency, and durable audit I/O. Do not describe them as end-to-end proxy latency. Publish raw machine-readable artifacts and compare like-for-like environments instead of promoting a single best run.

For an enterprise report, add a separate production-shaped load phase with pinned hardware, operating system, Python/package lock, workload corpus hash, warmup, concurrency matrix, sampling method, confidence intervals, error rate, RSS method, and all raw results. Report regressions as well as successes and disclose every excluded component.
