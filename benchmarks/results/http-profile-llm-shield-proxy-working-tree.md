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

Both were found by taking a competitor's better number seriously instead of dismissing it, and
both are in this project's own code.

> **No timing figure is published for this, deliberately.** An earlier version of this record
> carried per-event millisecond costs, a speed multiplier, and a head-to-head comparison against
> another gateway. The runner that produced them and its raw samples were **not retained**, and
> an independent re-measurement of the isolated rehydration path afterwards found the direction
> correct but the magnitude substantially smaller than the original note claimed. Unreproducible
> numbers were removed rather than caveated. A wrong performance claim about a named competitor
> is the same class of unretractable error as a wrong leak finding, and this project's whole
> credibility argument is that it does not make those. Any future claim needs a versioned runner
> committed to this repository alongside its raw output, measured end to end for every gateway
> compared.

What survives is structural, and each item is pinned by a regression test that fails if it is
reverted:

1. **One OpenTelemetry span per SSE delta.** A `TracerProvider` is always installed and only the
   exporter is gated, so the span was opened even with telemetry disabled. It was also wrong as
   telemetry: a 500-token answer emitted 500 spans. Now one span per stream, carrying the
   emitted-chunk count as an attribute.
2. **Two ASGI writes per SSE event.** The pipeline split the upstream on newlines and yielded
   every line separately, so each event cost one write for the data line and a second for the
   bare newline terminating it -- measured 2.02 `http.response.body` messages per event, the
   second one byte long. Output bytes are unchanged; only framing granularity is. Coalesced
   writes now target `MAX_SSE_LINE_LENGTH`. One encoded SSE line may exceed that write target so
   a longer rehydrated value is not truncated; an absolute per-piece ceiling of
   `MAX_PAYLOAD_SIZE_BYTES + MAX_SSE_LINE_LENGTH` fails closed on repeated-token amplification.

`tests/test_streaming_write_efficiency.py` verifies write-count reduction, byte and framing
equivalence, drip input, long-value pass-through, amplification rejection, reference-count
bounding, cancellation cleanup, and one span per stream. Those are correctness and resource
invariants -- **not** a latency threshold, and not evidence of a speed result.

**Two open policy questions**, recorded here because they bound what the `memory_bounded` check
means for this implementation and both are the maintainer's call:

1. `max_output_piece_bytes` is `MAX_PAYLOAD_SIZE_BYTES + MAX_SSE_LINE_LENGTH`, so raising the
   request-size limit silently raises the streaming amplification ceiling with it. It is an
   anti-amplification bound, not an absolute memory cap.
2. That rationale assumes every vault original arrived in the accepted request. Session-scoped or
   custom vault population may need its own bound; this is unverified.

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
pii-leak-benchmark
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
