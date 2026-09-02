# Published Conformance Results

## Published reference run

The repository includes a machine-readable v1.0.0 pre-release report at
`benchmarks/results/conformance-v1.0.0-pre-release-windows.json`. It covers all six scored
domains and includes the required timing distributions. The timing and allocation values apply
only to the recorded environment and operations. They are not end-to-end latency or process-wide
memory limits.

**Run:** 2026-09-02 on Windows 11, CPython 3.14.7, AMD64; 2,000 timing samples per operation.

**Source label:** `6573c0e60b34203ba4882a78d0da7812ddb514dc+working-tree`

**SHA-256:** `bfb50c51a272ce5150c982e34a6592cd36eb0d9bdf4f5378dc81c902b7c38158`

This is a **project-run measurement** from one submitter and one machine. No one outside the
project has reproduced it yet.

The `+working-tree` suffix means the run included source changes that had not been committed.
`6573c0e...` was the latest commit at the time; the report tested that commit plus the local
changes. The SHA-256 above identifies the report file. On every push to `main`, the
`Reproducible Public Benchmark` workflow creates a new report tied to the pushed commit.

| Domain | Latest status |
| :--- | :--- |
| Fragmentation safety | Pass - 11 partitions, no reported failures |
| Raw protected-data egress | Pass - email, SSN, and credit-card fixtures absent at tested boundary |
| SSE validity | Pass - valid JSON events, one `[DONE]`, valid termination, split UTF-8 preserved |
| Rehydration fidelity | Pass - exact equality; no placeholder in client-visible output |
| Audit integrity | Pass - two signatures verified; tamper negative control detected |
| Memory bound/measurement completeness | Pass - retained state within bound; allocation recorded; no RSS threshold |

Latency is reported but not scored. The former `latency_measurement` check only verified that
durations were non-negative, so it could not fail and was removed.

### In-process timing observations

| Operation | p50 | p95 | p99 |
| :--- | ---: | ---: | ---: |
| Empty-vault buffer | 0.6 us | 0.7 us | 0.7 us |
| Protected-token buffer | 5.2 us | 5.3 us | 5.8 us |

This report replaces an earlier report from before the streaming-path fixes. Do not compare the
two as a performance series because the code and measurement conditions differ. The figures
above measure in-process Python operations on one Windows machine. They exclude ASGI, HTTP, TLS,
network, model, concurrency, and durable audit I/O. The reported 32-byte peak covers only the
declared Python allocation scope; it is not process RSS.

## Independent reproductions

**There are no independent reproductions yet.**

[Reproduce a row](./reproducing), then [submit the raw report and pinned
configuration](./submitting). Use the synthetic fixture included with the harness. Do not use
confidential prompts or representative enterprise traffic.

## Cross-implementation HTTP results

The table includes incomplete work. “Not run” means there is no measurement; it is neither a pass
nor a failure. Every measured result must include both the raw report and a pinned target version
with its redacted configuration.

### Replication is counted, not averaged

All measured rows below were run by this project. The table records how many people have repeated
each target and configuration:

| Column | Meaning |
| :--- | :--- |
| **Runs** | complete artifacts that exist for this target and configuration |
| **Submitters** | how many **distinct** accounts produced them |
| **Versions** | the exact target versions or image digests measured |
| **Dates** | first and most recent run |
| **Status** | `unreplicated` until the floor is met; `disputed` when runs disagree |

The table requires **3 runs from 3 different submitters** before it treats a result as replicated.
Until then, the status is `unreplicated`. Project-run rows do not count as independent
reproductions. Three runs by one operator are still one independently configured setup.

The rule applies to every target. **LLM-Shield-Proxy has 1 run from 1 submitter, so its status is
also `unreplicated`.** A repeat that disagrees with a published result is useful and should be
submitted.

If two runs of the same target and configuration disagree, both remain in the table and the
status becomes `disputed`. The table will describe the difference instead of averaging it away.
Possible causes include configuration, version, and platform differences.

See [submitting a result](./submitting) for what a run must carry and how it is checked.

### What a row is allowed to say

The raw checks alone do not determine the published result. Each report records the product's
cited PII-redaction claim, whether redaction was enabled, and what the run measured. The harness
uses those fields to calculate `outcome`; the submitter cannot set it directly. The
[published schema](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json)
checks that calculation and rejects inconsistent edits.

| `outcome` | Row reads | Is it a leak finding? |
| :--- | :--- | :--- |
| `pass` | Pass | — |
| `fail` | Fail | **Yes.** Protected data reached the capture origin |
| `no-leak-profile-not-met` | No leak; does not meet the reversible-masking requirement | No |
| `not-applicable` | Not applicable — no redaction feature offered | No |
| `redaction-not-enabled` | Redaction available, not enabled for this run | No |
| `inconclusive` | Not a row — nothing correlated | No |
| `claim-unstated` | Not a row — no claim recorded | No |

A product that does not offer PII redaction receives no redaction verdict. A one-way anonymizer
that sends no protected values upstream but does not restore them also receives a separate
outcome. `leaked_entity_types` reports which protected data reached the capture.
`leak_evidence` says whether each match was literal or found only after normalization.

See the [hosted-gateway runbook](./hosted-gateway-runbook) for the per-vendor procedure.

| Target | `outcome` | Runs | Submitters | Versions | Dates | Status | Evidence |
| :--- | :--- | ---: | ---: | :--- | :--- | :--- | :--- |
| **Raw capture endpoint** — synthetic control, not a product | `fail` — raw fixtures reach the capture, three `literal` matches | 1 | 1 | harness negative control | 2026-09-01 | control | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-raw-capture-baseline.json) |
| **LLM-Shield-Proxy** - reference implementation | `pass` - 5/5 checks | 1 | 1 | `1.3.5` | 2026-09-01 | **`unreplicated`** | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.md) |
| **LiteLLM**, default configuration, no guardrail attached | `redaction-not-enabled` — a configuration statement, not a verdict. The other four checks pass | 1 | 1 | `litellm[proxy]==1.99.0`, CPython 3.12.3 | 2026-09-01 | `unreplicated` | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0-default.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0.md) |
| **LiteLLM + Presidio PII masking** (`guardrail: presidio`, `output_parse_pii: true`) | `no-leak-profile-not-met` — **no leak** (`leaked_entity_types: []`); the values were not restored to the client | 1 | 1 | same, Presidio images pinned by digest | 2026-09-01 | `unreplicated` | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0-presidio.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0.md) |
| **Portkey Gateway (OSS, self-hosted)**, default, no guardrails | `redaction-not-enabled` — a configuration statement, not a verdict. The other four checks pass | 1 | 1 | `portkeyai/gateway@sha256:97f094d9…5200d` (1.15.2) | 2026-09-01 | `unreplicated` | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2-default.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2.md) |
| **Portkey Gateway (OSS) + redaction** — one `default.regexReplace` hook, **tester-authored patterns**: this measures the guardrail transform engine, not Portkey's detector | `no-leak-profile-not-met` — **no leak** (`leaked_entity_types: []`); one-way replacement | 1 | 1 | same image | 2026-09-01 | `unreplicated` | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2-regexreplace.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2.md) |
| **Portkey (hosted platform)** | Not run — expected `no-leak-profile-not-met` | 0 | 0 | — | — | not run | — |
| **Presidio in front of an OpenAI-compatible mock** | Not run — expected `no-leak-profile-not-met` | 0 | 0 | — | — | not run | — |
| **Cloudflare AI Gateway** — [does not claim PII redaction](https://developers.cloudflare.com/ai-gateway/features/dlp/); DLP flags or blocks, neither redacts | Not run — expected `not-applicable` | 0 | 0 | — | — | not run | — |

Redaction claims, with citations: LiteLLM claims PII masking via its
[Presidio guardrail](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2); Portkey
claims [PII redaction and documents no rehydration](https://portkey.ai/docs/product/guardrails/pii-redaction);
Cloudflare AI Gateway does not claim redaction, so its expected outcome is `not-applicable`, not
`fail`.

All six measured runs used `loopback` capture mode and 3 iterations. Harness revision `1cef0ff`
produced the four third-party runs. The reference and control rows were regenerated after a
packaging change that altered import paths, the probe user-agent string, and one unused local
variable. It did not change inspection or scoring. See the
[hosted-gateway runbook](./hosted-gateway-runbook) for each vendor procedure.

The “expected” outcomes come from vendor documentation. They are not measured results. If a run
disagrees, the difference must be reviewed before publication.

Every measured row above was run by this project. The pinned configurations and raw reports are
public, but **none has been independently reproduced**. Every row, including this project's own,
therefore has the status `unreplicated`. See
[submitting a result](./submitting) and [governance](./governance).

### Third-party testing exposed a fixture defect

The old fixture used `person@example.invalid`, `123-45-6789`, and
`4532-1234-5678-9012`. These values were safe to publish but invalid examples of their claimed
data types. Presidio rejects the SSN prefix, the card fails its Luhn checksum, and `.invalid` has
no public suffix. Stock Presidio therefore detected none of them as an SSN, credit card, or email
address.

This gave the project's shape-only regex detector an unfair advantage over detectors that validate
values. **A run against LiteLLM with Presidio exposed the bias.** It reported
`leaked: ["SSN"]` with the old fixture and `leaked: []` when valid test values were substituted.
The project withheld that row until the fixture was fixed.

The replacement fixture uses valid test values from reserved, non-routable ranges. Values change
between runs while their formats stay the same. All rows were rerun, including the LiteLLM +
Presidio row above. The [fixture threat model](./fixture-threat-model) documents the correction,
a related false-positive case, and a decoder bug that the old fixture had hidden.

### Latency is not published at all

`client_observed_latency` checks only that every iteration produced a sample. It sets no speed
threshold, and three iterations are only a smoke test. **This table does not compare speed.**

A local diagnostic found two hot-path defects in this proxy. The per-target record describes the
defects and fixes. The project did not keep the runner or raw samples, and a later isolated
measurement found a much smaller effect. The project therefore publishes no numbers from that
diagnostic. Any future performance comparison must use a versioned runner, retain raw output,
measure end to end, and apply the same method to every gateway.

Raw OpenAI should not receive synthetic protected fixtures merely to populate a table. The local
capture endpoint supplies the correct pass-through negative control without transmitting those
values to a public provider. Additional targets are added only after their complete reproducible
configuration is available.
