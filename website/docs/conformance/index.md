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

Read the normative [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), review the [published results table](./results), [reproduce the local and HTTP profiles](./reproducing), or [submit a run](./submitting).

## The harness is a separate distribution, on purpose

The endpoint-neutral HTTP profile ships as **`pii-leak-benchmark`** — standard library plus
`httpx`, importing no gateway of any kind:

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1
```

It used to install as part of the reference proxy's own package, which was backwards twice
over: it put the name of one of the measured products on the neutral measurer, and it asked an
engineer at another gateway to install a competing gateway's stack in order to measure their
own. The specification keeps the Streaming Privacy Gateway name; only the tool was renamed. The
dependency direction is one-way and a regression test enforces it — the proxy may use the
benchmark, the benchmark never imports the proxy.

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

- **Implementation-affiliated:** a contributor to the implementation publishes a report and exact source revision.
- **Independently reproduced:** an unaffiliated party publishes the raw report for the same tagged revision.
- **Production-profiled:** a separately documented service-level experiment includes ASGI, HTTP/TLS, concurrency, networking, upstream behavior, error rate, and process RSS.

**Every published row today is project-run and none is independently reproduced**, so every one
of them — including this project's own — reads `unreplicated` in the table. A target does not
read as a verdict below 3 runs from 3 distinct submitters, and the maintainer's runs never count
toward anyone's replication. See [submitting a result](./submitting).

Passing the local harness does not establish population-level detector accuracy, a universal latency or memory ceiling, regulatory compliance, or immutable WORM retention.
