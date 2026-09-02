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

The JSON report records its schema and source revision, runtime details, iteration count, check
evidence, allocation observations, and p50/p95/p99 timing distributions. It does not include the
test values or reconstructed placeholders.

## Endpoint-neutral gateway profile

The endpoint-neutral profile is **not part of this package**. It ships as its own distribution,
`pii-leak-benchmark` - standard library plus `httpx`, importing no gateway - so measuring one
gateway does not require installing another. It exercises any OpenAI-compatible gateway over
HTTP once that gateway is configured to forward to the harness-owned capture upstream:

```bash
pip install pii-leak-benchmark
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name gateway-under-test \
  --target-version pinned-version \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
```

The capture returns the transformed prompt as one-character SSE events. The report checks the
configured-upstream boundary, SSE validity, fragmentation behavior, response fidelity, and
client-observed local latency. It leaves process memory and audit integrity outside this HTTP
profile because those properties require target-specific evidence.

## What the timing numbers mean

These are local in-process microbenchmarks. They exclude ASGI middleware, HTTP parsing, TLS, network latency, upstream model time, concurrency, and durable audit I/O. Do not describe them as end-to-end proxy latency. Publish raw machine-readable artifacts and compare like-for-like environments instead of promoting a single best run.

For an enterprise report, add a separate production-shaped load phase with pinned hardware, operating system, Python/package lock, workload corpus hash, warmup, concurrency matrix, sampling method, confidence intervals, error rate, RSS method, and all raw results. Report regressions as well as successes and disclose every excluded component.
