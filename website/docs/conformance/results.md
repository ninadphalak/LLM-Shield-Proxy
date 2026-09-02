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

**None. Zero rows in this document have been reproduced by anyone other than the maintainer.**
That is the largest evidence gap here and the highest-value contribution anyone can make.

[Reproduce a row](./reproducing), then [submit the artifact and its pinned
configuration](./submitting). Do not send representative enterprise traffic or confidential
prompts: the harness ships its own synthetic fixture precisely so you never have to.

## Cross-implementation HTTP results

The table is intentionally public before it is full. “Not run” is not a pass or a failure; it is
an explicit work item. Every result links its raw report and its pinned target
revision/image and redacted configuration — **never one without the other**.

### Replication is counted, not averaged

Every measured row below was produced by this project's maintainer, against a target he
installed himself, for a benchmark he wrote, beside a gateway he also wrote. Disclosure does not
repair that. Only other people running it does, so the table counts who ran what:

| Column | Meaning |
| :--- | :--- |
| **Runs** | complete artifacts that exist for this target and configuration |
| **Submitters** | how many **distinct** accounts produced them |
| **Versions** | the exact target versions or image digests measured |
| **Dates** | first and most recent run |
| **Status** | `unreplicated` until the floor is met; `disputed` when runs disagree |

**The floor is 3 runs from 3 distinct submitters.** Below it a target reads `unreplicated`
rather than a verdict, however clean the measurement was. The maintainer's runs never count
toward the replication of any row, including a competitor's: three runs from one person is one
setup measured three times.

That floor is not applied selectively. **LLM-Shield-Proxy's own row is 1 run by 1 submitter and
reads `unreplicated`** — it is the row with the strongest incentive behind it and the weakest
evidence under it. The competitor rows are the ones a LiteLLM or Portkey engineer has both the
means and the motive to re-run, and a run that contradicts one is the most useful thing anyone
can contribute here.

**Disagreements are published as disagreements.** Two runs of the same target and configuration
that reach different outcomes both stay in the table, the target is marked `disputed`, and the
difference is described. A disagreement is usually a finding — an undocumented default, a
version drift, a platform difference — and averaging it away destroys exactly the information
that made it worth publishing.

See [submitting a result](./submitting) for what a run must carry and how it is checked.

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

| Target | `outcome` | Runs | Submitters | Versions | Dates | Status | Evidence |
| :--- | :--- | ---: | ---: | :--- | :--- | :--- | :--- |
| **Raw capture endpoint** — synthetic control, not a product | `fail` — raw fixtures reach the capture, three `literal` matches | 1 | 1 | harness negative control | 2026-09-01 | control | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-raw-capture-baseline.json) |
| **LLM-Shield-Proxy** — the reference implementation, written by this benchmark's author | `pass` — 5/5 checks | 1 | 1 | `1.3.5` | 2026-09-01 | **`unreplicated`** | [report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.md) |
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
Cloudflare AI Gateway does not claim redaction at all, which is why publishing "Fail" for it
would be a smear rather than a result.

All six measured runs used `loopback` capture mode and 3 iterations. The four third-party runs
were produced by harness revision `1cef0ff`; the reference-implementation and control rows were
regenerated at the packaging split, whose diff to the harness is import paths, a probe
user-agent string and one dead local variable — no inspection or scoring logic changed. See the
[hosted-gateway runbook](./hosted-gateway-runbook) for the per-vendor procedure.

The "expected" outcomes above are predictions from vendor documentation, not results. They
are recorded so that a run which contradicts them is treated as a finding about the run
rather than quietly published.

Every measured row above was produced by this project's maintainer against a target he
installed himself. That is a conflict of interest. It is disclosed, the pinned configuration
and the raw artifact are published for each one, and **not one row has been independently
reproduced** — which is why every one of them, including this project's own, reads
`unreplicated`. See [submitting a result](./submitting) and [governance](./governance).

### The first third-party runs found a defect in the fixture, not in the gateways

The profile used to ship three protected values — `person@example.invalid`,
`123-45-6789` and `4532-1234-5678-9012` — chosen to be safe to publish. Every one was a
value a *validating* detector is built to reject: Presidio's SSN recognizer blacklists
that exact prefix, the card fails Luhn (checksum 68), and `.invalid` has no public
suffix. Stock Presidio returned no `US_SSN`, no `CREDIT_CARD` and no `EMAIL_ADDRESS` for
any of them.

So the benchmark scored a careful competitor worse than its own author's non-validating
regex engine. **That was a bias in this project's favour, and it was found by running
against a real third-party gateway rather than by reasoning.** LiteLLM with Presidio
enabled reported `leaked: ["SSN"]` against the old fixture and `leaked: []` against the
same run with detectable values substituted; that row was withheld rather than published.

The fixture was replaced: every value is now both a valid specimen a validating detector
recognises and drawn from reserved, non-routable space, and values vary per run with the
format held byte-identical. The withheld row is the LiteLLM + Presidio row above, now
published with an honest outcome. Full measurement, including the false-positive class
this removed and a fail-open decoder defect the old fixture had been hiding, is in the
[fixture threat model](./fixture-threat-model).

### Latency is not published at all

`client_observed_latency` enforces no threshold and gates on sample completeness, and three
iterations is a smoke test. **Nothing in this table is a speed comparison, and no timing figure
or speed multiplier is published anywhere in this repository.**

A maintainer-local diagnostic did find two real hot-path defects in **this** proxy, and those
are described in the per-target record. Its runner and raw samples were not retained, and an
independent re-measurement of the isolated rehydration path found the direction correct but the
magnitude substantially smaller than the original note claimed. So the defects and their fixes
are published; the numbers are not. A performance claim here needs a versioned runner committed
to this repository alongside its raw output, measured end to end, for every gateway compared —
and until that exists, a wrong speed claim about a named competitor would be the same class of
unretractable error as a wrong leak finding.

Raw OpenAI should not receive synthetic protected fixtures merely to populate a table. The local
capture endpoint supplies the correct pass-through negative control without transmitting those
values to a public provider. Additional targets are added only after their complete reproducible
configuration is available.
