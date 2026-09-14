---
sidebar_position: 3
title: Published results
---

# Published results

Every number on this page is generated from the JSON reports committed in this repository.
Nothing here is typed by hand. CI regenerates the page and fails if it disagrees with the
reports, so a figure on this page cannot outlive the artefact it came from.

Want to check one of these numbers yourself? [Reproduce the fragmentation
result](./reproduce-fragmentation) re-derives two of the rows below in about two minutes,
offline.

## How to read the outcomes

`fail` has one narrow meaning: a protected test value reached the capture server unmasked.
It is not a quality judgement, and a non-`pass` is not automatically a leak - see the
[outcome table](./reproducing#4-read-outcome) for what each value means.

**Request path** is a separate finding from the four response rates, and a row can be
clean on one and not the other. It reports whether unmasked test values reached the capture
server on the way *out* to the model provider, always paired with whether the target was
configured to prevent that:

- `configured, leaked: ...` is a control that was enabled and did not hold. This is a
  finding.
- `not configured (N types seen)` is **not** a finding. Request redaction was never
  enabled for that run, so the values were expected to pass through. Counting it against
  the product would be accusing it of a defect it was not asked to prevent.
- `not applicable` marks the reference policies, which are in-process models with no
  request-path claim to make.
- `claim not recorded` marks a measured product whose report did not record whether request
  redaction was configured. That is a gap in the evidence, not a result, and the row cannot
  be read either way until someone re-runs it with the claim supplied.

This is why `litellm-presidio` and `nemo-guardrails-0.24.0` both read `fail` while showing
`0` in both response leak columns, and why they are not the same result: LiteLLM had
request redaction configured and all four entity types left anyway; NeMo never enabled it.

One of the measured products is this project's own. Read the request-path column with that
in mind; the [benchmark readme](./benchmark_readme) states the conflict and the other limits
in full.

Two groups appear below and they are not comparable with each other:

- **Reference policies** are inspectors written inside the benchmark to isolate one
  variable. They are not products and were never shipped by anyone.
- **Measured gateways and libraries** are real software, at a pinned version, in a stated
  configuration. A row measures *that configuration*, not the product's best possible
  configuration, and certainly not the vendor's intent.

All results are **unreplicated** unless the table says otherwise. A result becomes
`replicated` only after three unaffiliated people submit a run of the same target and
configuration. See [submitting a result](./submitting).

<!-- BEGIN GENERATED: benchmarks/generate_results_page.py -->

_This section is generated from the committed JSON reports by
`benchmarks/generate_results_page.py`. Do not edit it by hand; edit the reports
or the generator. CI fails if this block and the reports disagree._

## v2 response-split profile

Corpus `30efa2eb65888844...` at seed `a1b2c3d4e5f60001`, inspector `94262e29a492ab6a`, 32 applicable cases per run (16 single-chunk and 16 fragmented). DeltaFrag is the fragmented leak rate minus the single-chunk leak rate; zero means boundary placement changed nothing.

### Reference policies (study controls, not products)

| Configuration | Fidelity | Leak, single | Leak, fragmented | DeltaFrag | Request path | Inconclusive | Outcome |
| :--- | ---: | ---: | ---: | ---: | :--- | ---: | :--- |
| `bounded-retention` | 1 | 0.125 | 0.125 | 0 | not applicable | 0/32 | `fail` |
| `chunk-local` | 1 | 0.125 | 1 | 0.875 | not applicable | 0/32 | `fail` |
| `gcp-dlp-chunk-local` | 1 | 0.5 | 1 | 0.5 | not applicable | 0/32 | `fail` |
| `gcp-dlp-retention` | 1 | 0.5 | 0.5 | 0 | not applicable | 0/32 | `fail` |
| `gcp-model-armor-chunk-local` | 1 | 0.75 | 1 | 0.25 | not applicable | 0/32 | `fail` |
| `gcp-model-armor-retention` | 1 | 0.75 | 0.75 | 0 | not applicable | 0/32 | `fail` |
| `passthrough` | 0 | 1 | 1 | 0 | not applicable | 0/32 | `fail` |
| `presidio-chunk-local` | 1 | 0.125 | 0.5 | 0.375 | not applicable | 0/32 | `fail` |
| `presidio-retention` | 1 | 0.125 | 0.125 | 0 | not applicable | 0/32 | `fail` |
| `redact-all` | 0 | 0.125 | 1 | 0.875 | not applicable | 0/32 | `fail` |
| `retention-plus-decoding` | 1 | 0 | 0 | 0 | not applicable | 0/32 | `pass` |

### Measured gateways and libraries

| Configuration | Fidelity | Leak, single | Leak, fragmented | DeltaFrag | Request path | Inconclusive | Outcome |
| :--- | ---: | ---: | ---: | ---: | :--- | ---: | :--- |
| `guardrails-ai-stream-validate` | 0 | 0.125 | 0.125 | 0 | not configured (4 types seen) | 0/32 | `fail` |
| `litellm-presidio` | 0 | 0 | 0 | 0 | configured, leaked: CARDPAN, EMAIL, SSN, USPHONE | 0/32 | `fail` |
| `llm-guard-buffered` | 1 | 0.3125 | 0.3125 | 0 | configured, leaked: USPHONE | 0/32 | `fail` |
| `llm-guard-chunk-local` | 1 | 0.25 | 0.75 | 0.5 | configured, leaked: USPHONE | 0/32 | `fail` |
| `llm-shield-proxy-1.6.0-response-off` | 1 | 1 | 1 | 0 | configured, clean | 0/32 | `fail` |
| `llm-shield-proxy-1.6.0-response-on` | 1 | 0.125 | 0.25 | 0.125 | configured, clean | 0/32 | `fail` |
| `nemo-guardrails-0.24.0` | 0 | 0 | 0 | 0 | not configured (4 types seen) | 8/32 | `fail` |
| `portkey-gateway-oss` | 1 | 1 | 1 | 0 | not configured (4 types seen) | 0/32 | `fail` |

**0 of 8 measured gateway and library configurations pass.** The only passing rows are reference policies: `retention-plus-decoding`.

### Seed sweeps

Mean across seeds, with minimum--maximum where the value varies.

| Configuration | Seeds | Fidelity | Leak, single | Leak, fragmented | DeltaFrag |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `guardrails-ai-stream-validate` | 6 | 0 | 0.125 | 0.125 | 0 |
| `litellm-presidio` | 6 | 0 | 0.0625 [0--0.1875] | 0.0625 [0--0.1875] | 0 |
| `llm-guard-buffered` | 6 | 0.9974 [0.9844--1] | 0.2917 [0.25--0.3125] | 0.2917 [0.25--0.3125] | 0 |
| `llm-guard-chunk-local` | 6 | 1 | 0.1667 [0--0.25] | 0.75 | 0.5833 [0.5--0.75] |
| `nemo-guardrails-0.24.0` | 6 | 0 | 0.0556 [0--0.1667] | 0.0556 [0--0.1667] | 0 |
| `portkey-gateway-oss` | 6 | 1 | 1 | 1 | 0 |
| `llm-shield-proxy-1.6.0-response-off` | 6 | 1 | 1 | 1 | 0 |
| `llm-shield-proxy-1.6.0-response-on` | 6 | 1 | 0.125 | 0.25 | 0.125 |
| `bounded-retention` | 12 | 1 | 0.125 | 0.125 | 0 |
| `chunk-local` | 12 | 1 | 0.125 | 1 | 0.875 |
| `passthrough` | 12 | 0 | 1 | 1 | 0 |
| `presidio-chunk-local` | 12 | 1 | 0.1667 [0.125--0.375] | 0.7188 [0.25--1] | 0.5521 [-0.125--0.875] |
| `presidio-retention` | 12 | 1 | 0.1615 [0.125--0.375] | 0.1615 [0.125--0.375] | 0 |
| `redact-all` | 12 | 0 | 0.125 | 1 | 0.875 |
| `retention-plus-decoding` | 12 | 1 | 0 | 0 | 0 |

### Evidence inventory

- v2 response-split reports: **95**
- FIDE v2.1 reports (separate 64-case corpus): **73**
- Seed-sweep aggregates: **9**

## v1.0.0 local in-process profile

`benchmarks/results/conformance-v1.0.0-1.6.0-windows.json`, schema `llm-shield.streaming-privacy-conformance/v1.0.0`, source revision `007994bf4e164cdd40f1b2eb0c8ac5d96df95d35`.

| Check | Result |
| :--- | :--- |
| audit_integrity | pass |
| fragmentation_safety | pass |
| memory_bounded | pass |
| raw_pii_egress | pass |
| rehydration_fidelity | pass |
| sse_validity | pass |

Report-level `passed`: **True**

| In-process operation | p50 | p95 | p99 |
| :--- | ---: | ---: | ---: |
| empty_vault_buffer | 0.9 us | 1.0 us | 1.6 us |
| no_op | 0.1 us | 0.2 us | 0.2 us |
| protected_token_buffer | 6.3 us | 6.8 us | 14.3 us |

Scope: in-process Python operations; excludes ASGI, HTTP, TLS, upstream, and model latency

<!-- END GENERATED -->

## Limits that apply to every row above

- The v2 profile observes network input and output only. Process RSS, audit integrity and
  detector accuracy on real traffic are out of scope.
- Fragmentation in the single-run rows is a two-part split at the value midpoint, not every
  possible split point. Exhaustive and union oracles live in `exhaustive-splits/` and
  `worst-case-splits/` under the results tree.
- Rates are over a fixed 32-case corpus in four entity types and two encodings. They do not
  estimate how often a production stream would place a boundary badly.
- The v1.0.0 timing figures are isolated in-process operations on one machine. They exclude
  ASGI, HTTP, TLS, network and model time, and they are not end-to-end proxy latency.

## Independent reproductions

None submitted yet. The six CI runners that reproduce the fragmentation rows on every push
are not independent - they run the author's code from the author's repository. See
[what a green run proves, and what it does not](./reproduce-fragmentation#what-a-green-run-proves-and-what-it-does-not).

To add yours, see [submitting a result](./submitting).

## Related

- [Benchmark readme](./benchmark_readme) - what the v2 profile measures and the findings a reviewer should check.
- [Reproduce the fragmentation result](./reproduce-fragmentation)
- [Reproduce the conformance report](./reproducing)
