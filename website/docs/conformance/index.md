# Streaming Privacy Gateway Tests

The [specification governance process](/docs/conformance/governance) defines normative changes, independent review, conflicts, versioning, and result labels.

These tests verify how a streaming privacy gateway handles known test values. They report functional results separately from timing, memory, and deployment-specific security claims.

The lab is Apache-2.0 licensed. The specification, vectors, runner, report schema, and reference implementations are fully inspectable and reusable without a license fee, account, hosted service, or paid edition.

## Six Scored Domains

| Domain | Question Answered |
| :--- | :--- |
| **Fragmentation Safety** | Can the client rebuild a value when its replacement token is split across SSE events? |
| **Upstream Data Exposure** | Did the gateway send any unmasked test value to the capture server? |
| **SSE Validity** | Is the response valid SSE, with valid JSON events and a single `[DONE]` marker? |
| **Value Restoration** | Does the client receive the expected original value without placeholder leakage? |
| **Audit Integrity** | Do the signature and sequence checks pass, and does the test detect tampered records? |
| **Memory Boundaries** | Does retained streaming state stay within limits, and does the report correctly track allocations? |

*Note: Latency is a publication requirement (SPG-LATENCY-1), not a scored check. Reports publish measured distributions under `microbenchmarks`.*

Review the [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), see the [published results table](./results), [reproduce the local and HTTP profiles](./reproducing), or [submit a run](./submitting).

## Independent Harness

The HTTP test suite ships as a separate, endpoint-neutral package named **`pii-leak-benchmark`**. This allows you to evaluate any gateway without installing the proxy itself. 

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1
```

## Cross-Implementation HTTP Profile

The HTTP profile tests any OpenAI-compatible gateway. It sends fictional personal data through the gateway to a harness-controlled capture server, which inspects the request for leaked test values. The capture server then streams the response back one character per SSE event so the benchmark can verify rehydration fidelity. 

This profile focuses purely on network input/output. It does not measure internal process RSS or audit integrity, which require separate local evidence.

## Claim Levels

- **Project Run:** A gateway contributor publishes the report and exact source version.
- **Independent Run:** Someone unaffiliated with the gateway publishes a report for the same version.
- **Production Profile:** A separate service test that includes HTTP/TLS, concurrent requests, network and model time, errors, and total process memory.

A result becomes `replicated` only after three unaffiliated individuals submit a run of the same gateway and configuration. Until then, the table marks the result as `unreplicated` (including LLM-Shield-Proxy's own results). See [submitting a result](./submitting).

Passing the local harness validates deterministic operations but does not establish population-level detector accuracy, a universal latency/memory ceiling, regulatory compliance, or immutable WORM retention.
