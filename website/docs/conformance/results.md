# Published Conformance Results

## Maintainer self-test

The repository includes a machine-readable v1.0.0 pre-release report at `benchmarks/results/conformance-v1.0.0-pre-release-windows.json`. It covers all seven required domains. Timing and allocation values are environment-scoped and are intentionally not promoted as total proxy latency or a universal memory ceiling.

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
| Latency measurement completeness | Pass - distributions recorded; no threshold enforced |
| Memory bound/measurement completeness | Pass - retained state within bound; allocation recorded; no RSS threshold |

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

| Target | Version/configuration | HTTP profile | Artifact |
| :--- | :--- | :--- | :--- |
| Raw capture endpoint | Harness negative control | Fail as expected: raw protected fixtures reach capture | [`http-profile-raw-capture-baseline.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-raw-capture-baseline.json) |
| LLM-Shield-Proxy | `1.3.4+working-tree`; maintainer self-test | Pass: 5/5 HTTP-profile checks | [Report](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.json) · [configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/results/http-profile-llm-shield-proxy-working-tree.md) |
| LiteLLM | Pending maintainer-neutral configuration | Not run | — |
| Portkey | Pending maintainer-neutral configuration | Not run | — |
| Presidio in front of an OpenAI-compatible mock | Pending adapter/configuration | Not run | — |
| Cloudflare AI Gateway | Pending testable configuration and account boundary | Not run | — |

Raw OpenAI should not receive synthetic protected fixtures merely to populate a table. The local
capture endpoint supplies the correct pass-through negative control without transmitting those
values to a public provider. Additional targets are added only after their complete reproducible
configuration is available.
