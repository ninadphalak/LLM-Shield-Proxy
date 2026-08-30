# Published Conformance Results

## Maintainer self-test

The repository includes a machine-readable v1.0.0 pre-release report at `benchmarks/results/conformance-v1.0.0-pre-release-windows.json`. It covers all seven required domains. Timing and allocation values are environment-scoped and are intentionally not promoted as total proxy latency or a universal memory ceiling.

**Run:** 2026-08-30 on Windows 11, CPython 3.14.7, AMD64; 10,000 timing samples per operation.

**Source label:** `e2b68294e5fed1a4e7a3905c0fd97dc1a02d564c+working-tree`

**SHA-256:** `8da1a5752ae312e0569d08899560fef2b22d4e5f666ccdf1db8c1fd8894b22bb`

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
| Empty-vault buffer | 26.9 us | 53.1 us | 73.5 us |
| Protected-token buffer | 41.4 us | 74.0 us | 102.7 us |

These are Windows in-process Python operation timings. They exclude ASGI, HTTP, TLS, network, upstream/model, concurrency, and durable audit I/O. The measured Python allocation peak was 4,656 bytes for the declared allocation scope; it is not process RSS.

## Independent reproductions

No unaffiliated reproduction has been published yet. This is an explicit evidence gap and the highest-priority community contribution.

[Reproduce the report](./reproducing) and submit the raw artifact. Do not send representative enterprise traffic or confidential prompts.
