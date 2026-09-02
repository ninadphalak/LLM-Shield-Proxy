# LLM-Shield-Proxy HTTP profile configuration

Maintainer working-tree self-test; not an independent result or a release artifact.

- Harness/source label: `1cef0ff` plus the working-tree changes described below
- Package label: `1.3.4+working-tree`
- Report SHA-256: `1dadfbd6a8f4b8a1ced35ecc5edc6b302941060d4bb431ed1e801190d6741356`
- Target: Uvicorn on `127.0.0.1:8899`
- Controlled upstream: `http://127.0.0.1:8765/v1` (capture mode: `loopback`)
- Request iterations: 3
- Outcome: `pass` — 5/5 checks, `leaked_entity_types: []`, `captured=3 correlated=3
  uninspectable=0 marker_max=5`, 133 events, response reconstructed

## What was actually running

This matters for any comparison, because a row that says "PII gateway" without saying
which detectors were loaded is not comparable to anything.

| Component | State for this run |
| :--- | :--- |
| Tier 1 pre-compiled regex | **on** |
| Tier 1 structural validation (issuer range, Luhn as a signal) | **on** |
| Tier 2 Shannon entropy | **on**, threshold 4.5 bits, minimum length 16 |
| Tier 3 quantized ONNX BERT-NER | **OFF** — `ENABLE_TIER3_ONNX_NER` defaults to `false` and no model was loaded |
| Masking | default `SYNTHETIC` |
| Agent-loop circuit breaker | on (default) |
| Audit durability | `best_effort` (default) |
| External telemetry / anonymous usage tracking | disabled |
| Rate limiting, blast radius, `ext_proc`, FinOps metering | disabled for this profile |

**Tier 3 NER was not loaded.** Any statement that this row reflects the ONNX NER path
would be false.

```text
HOST=127.0.0.1
PORT=8899
UPSTREAM_BASE_URL=http://127.0.0.1:8765
UPSTREAM_API_KEY=<controlled-capture-key>
OVERRIDE_CLIENT_AUTH=true
TELEMETRY_ENABLED=false
ANONYMOUS_USAGE_TRACKING=false
ENABLE_EXT_PROC=false
ENABLE_FINOPS_METERING=false
ENABLE_RATE_LIMITING=false
ENABLE_BLAST_RADIUS_LIMITS=false
```

The target used evaluation-only `OVERRIDE_CLIENT_AUTH=true` and injected a non-production
key for the controlled upstream. Secrets and synthetic fixture values are not written into
the report or this record.

## Two hot-path defects this benchmark found in this proxy

Both were found by taking a competitor's better number seriously instead of dismissing it,
and both are in this project's own code.

> **Evidence limit:** the diagnostic runner and raw timing/profile samples were not
> retained. The exact figures below are a maintainer-local historical observation, not
> independently auditable benchmark evidence. The checked-in regression tests verify the
> write/span/content invariants only. Re-measure with a versioned runner and raw artifact
> before citing any timing or percentage.

A three-iteration measurement had shown Portkey OSS at p50 52 ms against this proxy's
117 ms. Re-measured properly — 150 iterations per point, 25 warmup discarded, identical
fixed upstream for every gateway — the gap was real but differently shaped: this proxy's
**fixed** cost was the lowest of every gateway measured (9.9 ms for a one-event response
against Portkey's 36.9), and the entire gap was **per SSE event**, at 0.396 ms against
Portkey's 0.014 ms.

It was not detection. The same prompt with no PII in it, at the same length, was only
5.7 ms cheaper over 200 events.

| Layer | Cost per SSE event |
| :--- | ---: |
| Upstream directly | ~0 |
| Starlette + httpx forwarding raw chunks | ~0 |
| Starlette + httpx yielding once per event (framework floor) | 0.027 ms |
| Rehydration pipeline in isolation | 0.033 ms |
| **This proxy, end to end, before the fix** | **0.396 ms** |

1. **One OpenTelemetry span per SSE delta.** cProfile of the real ASGI app put 11% of all
   request-path CPU in `start_span`, and it ran even with telemetry disabled, because a
   `TracerProvider` is always installed and only the exporter is gated. It was also wrong
   as telemetry: a 500-token answer emitted 500 spans. Now one span per stream, carrying
   the emitted-chunk count as an attribute.
2. **Two ASGI writes per SSE event.** The pipeline split the upstream on newlines and
   yielded every line separately, so each event cost one write for the data line and a
   second for the bare newline terminating it — measured 2.02 `http.response.body`
   messages per event, the second one byte long. uvicorn charges per message. Output bytes
   are unchanged; only framing granularity is. Aggregate coalesced writes now target
   `MAX_SSE_LINE_LENGTH`. One encoded SSE line may exceed that write target so a longer
   rehydrated value is not truncated; an absolute per-piece ceiling of
   `MAX_PAYLOAD_SIZE_BYTES + MAX_SSE_LINE_LENGTH` fails closed on repeated-token
   amplification.

**After both fixes: 0.020 ms per event, a 20x reduction.** Verified on a token-by-token
upstream as well as a buffered one, so the gain is not an artifact of the test upstream.
`tests/test_streaming_write_efficiency.py` pins both; reverting either fails it and
nothing else. The 0.020 ms figure above was measured before the subsequent bounded-memory
guard was added; this record does not claim a new end-to-end timing for that guard.

## Over-redaction, and what was done about it

Separately measured on a 22-string corpus of ordinary business text — order numbers,
invoice ids, SKUs, ISBNs, tracking numbers, GL codes, cost centres, dates. None of it is
PII.

| | Strings with a false positive | False-positive spans |
| :--- | ---: | ---: |
| Before | 17 / 22 (77.3%) | 18 |
| Current fail-safe boundary | 17 / 22 (77.3%) | 18 |

Tier 1 now keeps structural validation as a **confidence signal**, never a
redaction gate:

- Every native 13–16 digit card match is redacted. Selected issuer prefixes and Luhn
  affect only an internal, unsurfaced confidence value: a private-label/newly assigned
  card may be absent from a finite table, and a typo can invalidate Luhn.
- Every native phone match is also retained. Bare 12–15 digit international numbers are
  plausible phone numbers even when a user omits `+` and separators.
- **The SSN pattern is deliberately unchanged.** Six of the remaining false positives are
  GL codes and cost centres shaped `ddd-dd-dddd`, which is structurally identical to a
  real SSN; the SSA rules that could be applied exclude none of them. Narrowing there
  would buy a cosmetic win with a real miss.

The false positives are kept because this boundary cannot prove they are not PII. An
attempted issuer-or-Luhn rejection reduced the corpus to 11 of 22 strings, but controlled
mutation showed it also leaked an unrecognised card after a transposition; the apparent
precision gain was rejected. `tests/test_tier1_validation_signal.py` pins private-label
card corruption and bare international phone negative controls through `detect_spans()`.

## Regenerate

```text
llm-shield-conformance
  --target-base-url http://127.0.0.1:8899/v1
  --target-api-key <local-evaluation-key>
  --target-name llm-shield-proxy
  --target-version 1.3.4+working-tree
  --iterations 3
  --capture-port 8765
  --redaction-claimed claimed
  --redaction-claim-citation "<project README>"
  --redaction-enabled
  --redaction-config-reference "<the component table above>"
  --json-out benchmarks/results/http-profile-llm-shield-proxy-working-tree.json
```

Regenerate from an exact commit and raise the iteration count before presenting this as a
release result or using latency observations comparatively.
