# Published Conformance Results

## Maintainer self-test

The repository includes a machine-readable v1.0.0 pre-release report at `benchmarks/results/conformance-v1.0.0-pre-release-windows.json`. It covers all six scored domains and publishes the required timing distributions. Timing and allocation values are environment-scoped and are intentionally not promoted as total proxy latency or a universal memory ceiling.

**Run:** 2026-08-30 on Windows 11, CPython 3.14.7, AMD64; 10,000 timing samples per operation.

**Source label:** `7e959d9d8f9ff6b85e05d9d9ce4642ad3cfb3fed+working-tree`

**SHA-256:** `57ef55181da29df9a5d74b138f0e1f030170db204e012a482c7e950575cdfafe`

Because the implementation changes are not yet committed, this is a transparent **maintainer pre-release self-test**, not a release-grade independently reproducible result. CI should regenerate the report from an exact commit SHA before a formal release.

| Domain | Latest status |
| :--- | :--- |
| Fragmentation safety | Pass - 11 partitions, no reported failures |
| Raw protected-data egress | Pass - email, SSN, and credit-card fixtures absent at tested boundary |
| SSE validity | Pass - valid JSON events, one `[DONE]`, valid termination, split UTF-8 preserved |
| Rehydration fidelity | Pass - exact equality; no placeholder in client-visible output |
| Audit integrity | Pass - two signatures verified; tamper negative control detected |
| Memory bound/measurement completeness | Pass - retained state within bound; allocation recorded; no RSS threshold |

Latency is published rather than scored: the former `latency_measurement` check could not fail, so it was removed. The distributions below are the evidence it claimed to be.

### In-process timing observations

| Operation | p50 | p95 | p99 |
| :--- | ---: | ---: | ---: |
| Empty-vault buffer | 23.1 us | 41.4 us | 57.6 us |
| Protected-token buffer | 34.3 us | 58.8 us | 84.2 us |

These are Windows in-process Python operation timings. They exclude ASGI, HTTP, TLS, network, upstream/model, concurrency, and durable audit I/O. The measured Python allocation peak was 4,656 bytes for the declared allocation scope; it is not process RSS.

## Independent reproductions

No unaffiliated reproduction has been published yet. This is an explicit evidence gap and the highest-priority community contribution.

[Reproduce the report](./reproducing) and submit the raw artifact. Do not send representative enterprise traffic or confidential prompts.

## Cross-implementation HTTP results

The table is intentionally public before it is full. “Not run” is not a pass or a failure; it is
an explicit work item. Results will link the raw report, pinned target revision/image, and
redacted configuration.

### What a row is allowed to say

A measurement is not a verdict. Every report carries an `outcome` derived from what the
product **claims** about PII redaction (with a citation), what was **configured** for the
run, and only then what was measured. The submitter supplies the claim and cannot type the
outcome; the [published schema](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json)
re-derives it, so a hand-edited report fails validation.

| `outcome` | Row reads | Is it a leak finding? |
| :--- | :--- | :--- |
| `pass` | Pass | — |
| `fail` | Fail | **Yes.** Protected data reached the capture origin |
| `no-leak-profile-not-met` | No leak; does not meet the reversible-masking requirement | No |
| `not-applicable` | Not applicable — no redaction feature offered | No |
| `redaction-not-enabled` | Redaction available, not enabled for this run | No |
| `inconclusive` | Not a row — nothing correlated | No |
| `claim-unstated` | Not a row — no claim recorded | No |

This exists because the harness can measure products it has no business judging. A gateway
that offers caching, routing and observability and never claimed to redact PII is not a
privacy failure; neither is a one-way anonymizer that leaks nothing and simply does not
restore values. Both would otherwise be printed as "Fail" next to this project's own "Pass",
which is an accusation a neutral referee cannot retract. `leaked_entity_types` remains the
only field that reports protected data reaching the capture, and `leak_evidence` now records
whether each finding was a `literal` match or one recovered only after normalization.

See the [hosted-gateway runbook](./hosted-gateway-runbook) for the per-vendor procedure.

| Target | Redaction claim | Version/configuration | `outcome` | Artifact |
| :--- | :--- | :--- | :--- | :--- |
| Raw capture endpoint | Synthetic control, not a product | Harness negative control | `fail` — raw fixtures reach the capture, three `literal` matches | [`http-profile-raw-capture-baseline.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-raw-capture-baseline.json) |
| LLM-Shield-Proxy | Claimed, enabled | `1.3.4+working-tree`; maintainer self-test | `pass` — 5/5 checks | [Report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.md) |
| LiteLLM (self-hosted) | Claims PII masking; [Presidio guardrail](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2) | `litellm[proxy]==1.99.0` on CPython 3.12.3; **default configuration, no guardrail attached** | `redaction-not-enabled` — a configuration statement, not a verdict. The other four checks pass | [Report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0-default.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0.md) |
| LiteLLM + Presidio PII masking | as above | `litellm[proxy]==1.99.0`, `guardrail: presidio`, `output_parse_pii: true`, stock Presidio images pinned by digest | **Held back — not a row.** The shipped fixture is undetectable by Presidio, so the measured `fail` would score the fixture, not the product. [Raw artifact](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0-presidio-NOT-A-VERDICT.json) · [evidence and controlled experiment](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-litellm-1.99.0.md) |
| Portkey Gateway (OSS, self-hosted) | Claims redaction; [does not document rehydration](https://portkey.ai/docs/product/guardrails/pii-redaction) | `portkeyai/gateway@sha256:97f094d9…5200d` (1.15.2); **default configuration, no guardrails** | `redaction-not-enabled` — a configuration statement, not a verdict. The other four checks pass | [Report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2-default.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2.md) |
| Portkey Gateway (OSS, self-hosted) + redaction | as above | same image; one `default.regexReplace` before-request hook. **Tester-authored patterns** — this measures Portkey's guardrail transform engine, not Portkey's detector | `no-leak-profile-not-met` — **no leak** (`leaked_entity_types: []`); one-way replacement does not meet the reversible-masking requirement | [Report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2-regexreplace.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-portkey-gateway-oss-1.15.2.md) |
| Portkey (hosted platform) | as above | Pending run with `Portkey Pro PII` or `Regex Match` guardrails enabled | Not run — expected `no-leak-profile-not-met` | — |
| Presidio in front of an OpenAI-compatible mock | Claims anonymization; `replace`/`hash`/`mask` do not rehydrate | Pending adapter/configuration | Not run — expected `no-leak-profile-not-met` | — |
| Cloudflare AI Gateway | **Does not claim PII redaction.** [DLP flags or blocks; neither redacts](https://developers.cloudflare.com/ai-gateway/features/dlp/) | Pending run | Not run — expected `not-applicable` | — |

The "expected" outcomes above are predictions from vendor documentation, not results. They
are recorded so that a run which contradicts them is treated as a finding about the run
rather than quietly published.

Every measured row above was produced by this project's maintainer against a target they
installed themselves. That is a conflict of interest. It is disclosed, the pinned
configuration and the raw artifact are published for each one, and none of them has been
independently reproduced. See [governance](./governance).

### The first third-party runs found a defect in the fixture, not in the gateways

The three protected values in the shipped fixture — `person@example.invalid`, `123-45-6789`
and `4532-1234-5678-9012` — were chosen to be safe to publish. Every one of them is a value a
*validating* detector is built to reject:

| Fixture value | Why a validating detector rejects it |
| :--- | :--- |
| `123-45-6789` | Presidio's `US_SSN` recognizer invalidates the sequential digit run. `456-78-9012` scores 0.85 in the same sentence |
| `4532-1234-5678-9012` | Luhn checksum is 68. Presidio's `CREDIT_CARD` recognizer validates the checksum; `4111-1111-1111-1111` scores 1.0 |
| `person@example.invalid` | `.invalid` is not a real TLD; Presidio reports `URL` at 0.5, never `EMAIL_ADDRESS`. `bob@example.com` scores 1.0 |

Run against the whole fixture prompt, stock Presidio returns exactly two findings —
`DATE_TIME` over the card number and `URL` over part of the email — and nothing at all for the
SSN. So a gateway whose detector validates its inputs leaks the SSN here while behaving
correctly on real data, and a gateway whose detector is pure pattern-matching (this project's
included) passes.

That asymmetry is measured, it is in this profile's favour, and it is the reason the
LiteLLM + Presidio row above is held back rather than published as a `fail`. It is a
separate problem from the fixture being *gameable*
([threat model](./fixture-threat-model)) and it is not yet fixed. Until it is, a `fail`
against a validating detector is not publishable as a verdict.

Raw OpenAI should not receive synthetic protected fixtures merely to populate a table. The local
capture endpoint supplies the correct pass-through negative control without transmitting those
values to a public provider. Additional targets are added only after their complete reproducible
configuration is available.
