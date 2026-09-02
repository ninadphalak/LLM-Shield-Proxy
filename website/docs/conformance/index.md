# Streaming privacy gateway tests

The [specification governance process](/docs/conformance/governance) defines normative changes,
independent review, conflicts, versioning, and result labels.

These tests check how a streaming privacy gateway handles known test values. They report functional
results separately from timing, memory, and deployment-specific security claims.

The lab is Apache-2.0 licensed. The specification, vectors, runner, report schema, and implementation are inspectable and reusable without a license fee, account, hosted service, or paid edition.

## Six scored domains, plus published timings

| Domain | Question answered |
| :--- | :--- |
| Fragmentation safety | Can the client rebuild a value when its replacement token is split across SSE events? |
| Upstream data exposure | Did the gateway send any unmasked test value to the capture server? |
| SSE validity | Is the response valid SSE, with valid JSON events and one `[DONE]` marker? |
| Value restoration | Does the client receive the expected original value, with no replacement text left behind? |
| Audit integrity | Do the signature and sequence checks pass, and does the test detect a changed record? |
| Memory | Does retained streaming state stay within its limit, and does the report identify what it measured? |

Latency is a **publication** requirement (SPG-LATENCY-1), not a scored check. The old check only verified that elapsed times were non-negative, so it could not distinguish a good implementation from a bad one. Reports still publish the measured distributions under `microbenchmarks`.

Read the normative [Streaming Privacy Gateway Conformance Specification v1.0.0](./specification-v1), review the [published results table](./results), [reproduce the local and HTTP profiles](./reproducing), or [submit a run](./submitting).

## The harness is a separate package

The HTTP test ships as **`pii-leak-benchmark`**. Its only third-party Python dependency is `httpx`,
and it does not import code from any gateway:

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1
```

The test tool used to ship inside LLM-Shield-Proxy. Other gateway teams had to install the proxy to
run it, so the tool now ships as a separate package. The specification name did not change.

The proxy may depend on the benchmark. The benchmark never imports the proxy, and a regression
test enforces that boundary.

## Cross-implementation HTTP profile

The HTTP test sends fictional personal data through an OpenAI-compatible gateway to a capture
server started by the benchmark. That server checks the request for the test values. It then sends
the response back one character per SSE event so the benchmark can verify that the client still
receives a valid and complete response. The test does not import the gateway's detector or
streaming code.

The HTTP profile is narrower than the local profile. It does not measure process RSS or audit
integrity on a remote target. Those claims require separate evidence.

## Claim levels

- **Project run:** a contributor to the gateway publishes the report and exact source version.
- **Independent run:** someone unaffiliated with the gateway publishes a report for the same version.
- **Production profile:** a separate service test includes HTTP/TLS, concurrent requests, network and
  model time, errors, and total process memory.

Every published product result currently has one run from this project's maintainer. A result
becomes `replicated` only after three different people each submit a run of the same gateway and
configuration. Maintainer runs do not count toward those three independent runs. Until then, the
table marks the result `unreplicated`, including the LLM-Shield-Proxy result. See
[submitting a result](./submitting).

Passing the local harness does not establish population-level detector accuracy, a universal latency or memory ceiling, regulatory compliance, or immutable WORM retention.
