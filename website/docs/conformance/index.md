# Open Streaming-Privacy Conformance Lab

The [specification governance process](/docs/conformance/governance) defines normative changes,
independent review, conflicts, versioning, and result labels.

The Open Conformance Lab is the vendor-neutral evidence surface for streaming privacy gateways. It separates correctness, security boundaries, and environment-scoped measurements so implementers can reproduce a claim instead of trusting a product tagline.

The lab is Apache-2.0 licensed. The specification, vectors, runner, report schema, and implementation are inspectable and reusable without a license fee, account, hosted service, or paid edition.

## Six scored domains, plus published timings

| Domain | Question answered |
| :--- | :--- |
| Fragmentation safety | Are registered protected placeholders safe across every tested split, including one-character delivery? |
| Raw-PII egress | Do any protected values in the declared vector reach the exact serialized configured-upstream boundary? |
| SSE validity | Does output remain parseable OpenAI-style SSE, preserve one `[DONE]`, and survive a UTF-8 mid-codepoint split? |
| Rehydration fidelity | Does the client receive the exact expected reconstructed value, without placeholder residue? |
| Audit integrity | Do chain, sequence, fingerprint, and Ed25519 checks pass-and does a tampered negative control fail? |
| Memory | Is retained streaming state bounded and is the measurement labeled correctly as allocation data or process RSS? |

Latency remains a **publication** requirement (SPG-LATENCY-1), not a scored check. The former `latency_measurement` check gated on percentiles of monotonic-clock deltas being non-negative, which cannot fail under any implementation or any input; a check that cannot fail is not evidence. The distributions are still published under `microbenchmarks`.

Read the normative [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), review the [published results table](./results), or [reproduce the local and HTTP profiles](./reproducing).

## Cross-implementation HTTP profile

The endpoint-neutral profile sends a synthetic fixture through an OpenAI-compatible gateway to a
harness-owned capture upstream. The capture checks the serialized upstream request, emits the
observed content as one-character SSE events, and lets the harness verify downstream SSE and
reconstruction. This design can evaluate different gateways without importing their detector or
streaming classes.

The HTTP profile is narrower than the local profile: it does not remotely infer
process RSS or audit integrity. Those properties require separate artifacts rather than a
fabricated pass.

## Claim levels

- **Self-tested:** the implementation owner publishes a passing report and exact source revision.
- **Independently reproduced:** an unaffiliated party publishes the raw report for the same tagged revision.
- **Production-profiled:** a separately documented service-level experiment includes ASGI, HTTP/TLS, concurrency, networking, upstream behavior, error rate, and process RSS.

Passing the local harness does not establish population-level detector accuracy, a universal latency or memory ceiling, regulatory compliance, or immutable WORM retention.
