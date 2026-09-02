# Open Streaming-Privacy Conformance Lab

The [specification governance process](/docs/conformance/governance) defines normative changes,
independent review, conflicts, versioning, and result labels.

The Open Conformance Lab publishes repeatable tests for streaming privacy gateways. It separates correctness checks from security boundaries and environment-specific measurements.

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

Latency is a **publication** requirement (SPG-LATENCY-1), not a scored check. The old check only verified that elapsed times were non-negative, so it could not distinguish a good implementation from a bad one. Reports still publish the measured distributions under `microbenchmarks`.

Read the normative [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), review the [published results table](./results), [reproduce the local and HTTP profiles](./reproducing), or [submit a run](./submitting).

## The harness is a separate package

The endpoint-neutral HTTP profile ships as **`pii-leak-benchmark`** — standard library plus
`httpx`, importing no gateway of any kind:

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1
```

The harness used to be part of the reference proxy package. That made other gateway teams
install a competing gateway to run the test. It also tied the supposedly neutral test tool to
one product. The specification name is unchanged, but the tool now ships separately.

The proxy may depend on the benchmark. The benchmark never imports the proxy, and a regression
test enforces that boundary.

## Cross-implementation HTTP profile

The endpoint-neutral profile sends a synthetic fixture through an OpenAI-compatible gateway to a
harness-owned capture upstream. The capture checks the serialized upstream request, emits the
observed content as one-character SSE events, and lets the harness verify downstream SSE and
reconstruction. This design can evaluate different gateways without importing their detector or
streaming classes.

The HTTP profile is narrower than the local profile. It does not measure process RSS or audit
integrity on a remote target. Those claims require separate evidence.

## Claim levels

- **Implementation-affiliated:** a contributor to the implementation publishes a report and exact source revision.
- **Independently reproduced:** an unaffiliated party publishes the raw report for the same tagged revision.
- **Production-profiled:** a separately documented service-level experiment includes ASGI, HTTP/TLS, concurrency, networking, upstream behavior, error rate, and process RSS.

**Every published row is currently project-run. None has been independently reproduced.** Each
row therefore says `unreplicated`, including the reference implementation. A result needs three
runs from three submitters before the table presents a verdict. Maintainer runs do not count
toward that total. See [submitting a result](./submitting).

Passing the local harness does not establish population-level detector accuracy, a universal latency or memory ceiling, regulatory compliance, or immutable WORM retention.
