# Open Streaming-Privacy Conformance Lab

The Open Conformance Lab is the vendor-neutral evidence surface for streaming privacy gateways. It separates correctness, security boundaries, and environment-scoped measurements so implementers can reproduce a claim instead of trusting a product tagline.

The lab is Apache-2.0 licensed. The specification, vectors, runner, report schema, and implementation are inspectable and reusable without a license fee, account, hosted service, or paid edition.

## Seven conformance domains

| Domain | Question answered |
| :--- | :--- |
| Fragmentation safety | Are registered protected placeholders safe across every tested split, including one-character delivery? |
| Raw-PII egress | Do any protected values in the declared vector reach the exact serialized configured-upstream boundary? |
| SSE validity | Does output remain parseable OpenAI-style SSE, preserve one `[DONE]`, and survive a UTF-8 mid-codepoint split? |
| Rehydration fidelity | Does the client receive the exact expected reconstructed value, without placeholder residue? |
| Audit integrity | Do chain, sequence, fingerprint, and Ed25519 checks pass-and does a tampered negative control fail? |
| Latency | Are warmup, scope, iterations, unit, and distribution statistics published without presenting an isolated operation as end-to-end latency? |
| Memory | Is retained streaming state bounded and is the measurement labeled correctly as allocation data or process RSS? |

Read the normative [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), review the [latest reproducible result](./results), or [reproduce it locally](./reproducing).

## Claim levels

- **Self-tested:** the implementation owner publishes a passing report and exact source revision.
- **Independently reproduced:** an unaffiliated party publishes the raw report for the same tagged revision.
- **Production-profiled:** a separately documented service-level experiment includes ASGI, HTTP/TLS, concurrency, networking, upstream behavior, error rate, and process RSS.

Passing the local harness does not establish population-level detector accuracy, a universal latency or memory ceiling, regulatory compliance, or immutable WORM retention.
