# Public Benchmark Reporting Protocol

Use this protocol for release artifacts, comparative studies, and enterprise pilot
reports. A credible report must make unsuccessful and excluded results as visible as
favorable results.

## 1. Freeze the experiment

Record before execution:

- research question, primary endpoint, and pass/fail threshold
- commit SHA, package lock hash, container digest, and configuration with secrets removed
- CPU model/count, RAM, storage, OS/kernel, Python/runtime, power governor, and virtualization
- workload corpus description and SHA-256; use licensed, synthetic, or organization-approved data
- warmup, sample count, duration, concurrency, repetitions, timeout, and random seed
- every component included or excluded: ASGI, HTTP, TLS, network, model, logging, and durable `fsync`

Do not tune thresholds after seeing the results. If the protocol changes, issue a new
version and explain why.

## 2. Separate three result classes

1. **Correctness/conformance:** fragmentation partitions, SSE syntax, `[DONE]`,
   privacy-boundary leak checks, and error behavior.
2. **In-process components:** nanosecond or microsecond distributions for isolated
   functions. Never label these end-to-end latency.
3. **Production-shaped service tests:** requests/second, time-to-first-byte, inter-chunk
   delay, end-to-end p50/p95/p99/p99.9, error/drop rates, CPU, RSS/peak RSS, and audit
   persistence latency under concurrency.

Always report absolute measurements, not only ratios. Include all repetitions,
median-of-runs, dispersion or confidence intervals, and sample counts. A result below
timer resolution must be aggregated into batches.

## 3. Required comparison matrix

Run each service test with the same client, host isolation, corpus, upstream mock, and
connection settings:

| Variant | Purpose |
| :--- | :--- |
| direct mock upstream | network/client baseline |
| pass-through proxy | framework and routing cost |
| Tier 1 only | structured PII cost |
| Tier 1 + Tier 2 | secret-scanning cost |
| Tier 1 + Tier 2 + Tier 3 | optional NER cost |
| best-effort audit | default asynchronous evidence path |
| durable audit | storage acknowledgement and `fsync` cost |

Test at multiple payload sizes and concurrency levels through saturation. Include
malformed and adversarial payloads and publish the error taxonomy.

## 4. Artifact layout

```text
benchmark-report/
  README.md                 # claim, scope, date, conclusion
  protocol.json             # frozen parameters and thresholds
  environment.json          # machine/runtime/container metadata
  conformance.json          # llm-shield-proxy benchmark output
  raw/                      # per-request/per-run observations
  summaries/                # aggregates generated from raw data
  plots/                    # reproducibly generated figures
  checksums.sha256
  reproduce.ps1             # or reproduce.sh; one-command entry point
```

Archive stdout/stderr and failed runs. Generate charts and tables from `raw/`; do not
manually transcribe headline values.

## 5. Reproduction and review

```bash
llm-shield-proxy benchmark --iterations 2000 --json-out conformance.json
```

That command runs the LOCAL in-process profile. To measure a gateway over HTTP -- this one or
any other OpenAI-compatible one -- use the neutral harness, which is a separate distribution
(stdlib plus httpx, importing no gateway): `pip install pii-leak-benchmark`, then
`pii-leak-benchmark --target-base-url <url>`. See
`website/docs/conformance/submitting.md` for what a published row must carry.

The packaged command reports conformance and explicitly scoped in-process component
observations. A production-shaped service report must add the frozen environment,
baseline matrix, concurrency runs, raw observations, and process-level resource
measurements described above. Do not relabel an isolated function measurement as proxy
overhead.

Run on at least two independent machines or hosted runners, then ask an unaffiliated
maintainer or practitioner to reproduce the tagged release. Publish deviations and the
reviewer-supplied artifact. Use a DOI-backed release archive when the report is stable,
while keeping the repository as the living implementation.

## Claim checklist

- Is each headline tied to a report version and exact environment?
- Are p95/p99 based on enough samples and accompanied by failures?
- Are memory figures labeled as RSS, peak RSS, or Python allocation, without mixing them?
- Did the test inspect the exact request bytes sent to the model provider?
- Are WORM, compliance, and cryptographic claims limited to what was actually tested?
- Can a third party execute the protocol without private infrastructure or data?
