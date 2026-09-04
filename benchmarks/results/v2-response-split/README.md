# v2 response-split emitter — measured run

**Run:** 2026-09-04, project-run, single machine. **Not independently reproduced.**

- Emitter: `pii-leak-benchmark/pii_leak_benchmark/v2_emitter.py` (committed)
- Reproduce: `python -m pii_leak_benchmark.v2_emitter --validate`
- Schema: `spec/v2.0.0/http-profile.schema.json`
- Reports: `passthrough.json`, `redact-all.json`, `chunk-local.json`,
  `bounded-retention.json`, `retention-plus-decoding.json`,
  `presidio-chunk-local.json`, `presidio-retention.json`
- The two `presidio-*` policies require a live analyzer on `127.0.0.1:5002`. Select a
  subset with `--only`, e.g. `--only chunk-local,bounded-retention`.

**What this establishes:** `spec/v2.0.0`'s echo/injection response split is now a
**demonstrated** design, not only a specified one. Before this run the v2 directory held a
schema and a README with no emitter and no report; C3 in `manuscript-v2.md` was a design
claim a reviewer could reasonably discount.

---

## 1. Result

**Reference policies** (models, chosen to occupy the corners of the space):

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag | Outcome |
|---|---:|---:|---:|---:|---|
| `passthrough` | 0.0 | 1.0 | 1.0 | 0.0 | fail |
| `redact-all` | 0.0 | 0.0 | 1.0 | 1.0 | fail |
| `chunk-local` | 1.0 | 0.0 | 1.0 | 1.0 | fail |
| `bounded-retention` | 1.0 | 0.0 | 0.3333 | 0.3333 | fail |
| `retention-plus-decoding` | 1.0 | 0.0 | 0.0 | 0.0 | **pass** |

**A real detector** — a live `mcr.microsoft.com/presidio-analyzer` container on
`127.0.0.1:5002`, stock recognizer registry, queried over HTTP per delta:

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag | Outcome |
|---|---:|---:|---:|---:|---|
| `presidio-chunk-local` | 1.0 | 0.0 | 1.0 | **1.0** | fail |
| `presidio-retention` | 1.0 | 0.0 | 0.3333 | **0.3333** | fail |

All seven reports validate against `spec/v2.0.0/http-profile.schema.json`
(`jsonschema` 4.26.0, Draft 2020-12).

## 2. The five findings a reviewer should check

### 2.1 No single global response-path policy passes

`passthrough` and `redact-all` fail in **opposite directions**. Passthrough returns
everything, so the echo segment is untouched but every injected value reaches the client.
Redact-all suppresses injected values in the single-chunk condition but destroys the echo,
scoring FidelityRate 0.0.

This is the discriminating property the split exists for. A gateway is not being asked to
apply one rule to the response; it is being asked to apply **two opposite rules to two
segments of the same response**, and a profile that measured only one direction could not
tell a correct gateway from a destructive one.

*Note the asymmetry in the table:* `redact-all` scores FidelityRate 0.0 for the same reason
`passthrough` scores LeakRate 1.0. Neither is a partial pass.

### 2.2 DeltaFrag separates policies that are otherwise indistinguishable

`chunk-local`, `bounded-retention` and `retention-plus-decoding` are **identical under the
single-chunk condition** — all three score LeakRate 0.0 and FidelityRate 1.0. A benchmark
that only sent whole values inside single chunks would rank them equal.

Under the adversarial condition they separate: 1.0, 0.3333, 0.0. DeltaFrag is exactly that
gap, and it is the number that reports how much of a gateway's apparent correctness is an
artefact of being tested on unfragmented input.

### 2.3 The same result holds for a real detector, not just the models

`presidio-chunk-local` and `presidio-retention` use a **live Presidio analyzer** — same
container, same recognizer registry, same fixture, same corpus. The only difference between
the two rows is whether a chunk boundary is allowed to fall inside a value.

**DeltaFrag falls from 1.0 to 0.3333** on that change alone. Both score LeakRate 0.0 under
the single-chunk condition, so a benchmark that never fragmented would rank them identical.

Two things this rules out:
- **It is not an artefact of a toy detector.** The reference `chunk-local` policy and the
  real Presidio one produce the same DeltaFrag, 1.0, by the same mechanism.
- **It is not a Presidio defect.** Presidio makes no streaming claim; applying it per chunk
  is the integrator's decision, and the property belongs to the integration pattern. The
  rehydration half is the wrapper's, not Presidio's, so FidelityRate here does not describe
  Presidio at all.

### 2.4 Retention fixes fragmentation and does **not** fix encoding

`bounded-retention` holds back a bounded tail so no value straddles a chunk boundary
undetected. It still leaks 1 case of 6.

The leaking case is `entity=EMAIL, encoding=percent, fragmentation=adversarial,
carrier=sse-json-field`. Cause: a percent-encoded address contains `%40`, not `@`, so the
detector never fires however much buffer is held. Per-axis breakdown from
`bounded-retention.json`:

| Axis value | leak_rate | applicable |
|---|---:|---:|
| `encoding=plain` | 0.0 | 3 |
| `encoding=percent` | 0.3333 | 3 |
| `fragmentation=single_chunk` | 0.0 | 3 |
| `fragmentation=adversarial` | 0.3333 | 3 |

`retention-plus-decoding` adds decoding before detection and closes it. **Encoding and
fragmentation are independent defects requiring independent mitigations**, which is the
argument for keeping them as separate corpus axes rather than folding them together.

**The real detector has the same blind spot.** `presidio-retention` leaks the identical
case — `EMAIL / percent / adversarial` — with `encoding=plain` at 0.0 and
`encoding=percent` at 0.3333. Presidio does not percent-decode before analysing either, so
this is a property of the integration pattern rather than of the model detector, and the
cross-check is the reason to trust the reference-policy row.

### 2.5 The v2 corpus block cannot be satisfied by a partial run

`corpus.coverage.axes` has `minItems: 4` and the enum is exactly
`entity, encoding, fragmentation, carrier`. A single-axis sweep cannot produce a valid v2
report. That is the schema working as designed, and it is why this emitter carries a real
pairwise covering array rather than a fragmentation-only sweep.

Generated array: **6 cases, 30 of 30 pairs covered, `proof_complete: true`**, recomputed
from the emitted cases rather than asserted.

---

## 3. How to verify this without trusting the numbers above

```bash
# 1. Re-run. Values are drawn fresh each run; rates should reproduce, values will not.
python -m pii_leak_benchmark.v2_emitter --validate

# 2. Confirm the covering-array proof independently.
python -c "
from pii_leak_benchmark.v2_emitter import covering_array, _all_pairs, _pairs_of
ca = covering_array(); cov = set()
for c in ca: cov |= _pairs_of(c)
print(len(ca), 'cases |', len(_all_pairs() & cov), 'of', len(_all_pairs()), 'pairs')
"

# 3. Validate a stored report against the published schema yourself.
python -c "
import json, jsonschema
schema = json.load(open('spec/v2.0.0/http-profile.schema.json'))
report = json.load(open('benchmarks/results/v2-response-split/chunk-local.json'))
jsonschema.Draft202012Validator(schema).validate(report)
print('valid')
"
```

**Adversarial checks worth running** — each targets a way this result could be hollow:

1. **Is the gateway being handed the answers?** Grep `v2_emitter.py` for any path where a
   policy receives `segments.injection`. It should not exist: policies get only the request
   prompt and their own vault. The detectors in `_DETECTORS` are generic regexes.
2. **Is `segment_separation` doing real work?** Force a collision by making
   `build_segments` return the same fixture for both segments and confirm the run reports
   `outcome: inconclusive` rather than a leak.
3. **Is the adversarial split actually splitting?** Set
   `fragmentation=adversarial` and assert `_injection_events` returns two pieces whose
   concatenation is the encoded value and neither of which contains it whole.
4. **Is FidelityRate measuring rehydration or just echo?** Confirm `passthrough` scores 0.0
   — it forwards the *masked* prompt, so a policy that does nothing must fail fidelity. If
   passthrough ever scores 1.0, the upstream is echoing unmasked text and the measurement
   is void.
5. **Does the client inspector over-reach?** `_present` normalizes and percent-decodes. Confirm
   it does not fold ASCII in a way that manufactures matches — the v1 harness has a
   documented false-positive incident from exactly that.

---

## 4. Limits — stated in the reports and repeated here

- **Reference policies, not products.** No third-party gateway is measured, named or
  ranked. These are deliberate models chosen to occupy the corners of the space.
- **Loopback transport**, single machine, project-run, unreplicated.
- **6 cases**, pairwise not exhaustive. Three entity types, two encodings, two carriers,
  two fragmentation conditions.
- **Fragmentation is a single midpoint split**, not every split point. The v1 oracle in
  `llm_shield_proxy/conformance/local.py` is the exhaustive one, over a different property.
- **Latency is loopback and in-process.** It is not gateway overhead on a network and must
  not be cited as such.
- **No generative corpus** behind the case definitions; `corpus.sha256` digests the six
  case definitions this module emits and nothing more.
- The `seed` field is recorded but **values are not reproducible from it** — the fixture
  generator is not seeded. This is a real gap against the schema's intent, which is that a
  seed reproduces the drawn values. Recorded rather than papered over.

## 5. What this does not do

It does not measure a full **gateway product**. The Presidio rows measure a real, widely
deployed *detector* inside a wrapper written here — not LiteLLM's guardrail, not
LLM-Shield-Proxy, not any vendor's shipped streaming path. The rehydration half is the
wrapper's in every row.

Running the profile against LiteLLM's Presidio guardrail and against LLM-Shield-Proxy
itself is the next step. `litellm` is not installed in this environment; the Presidio
containers are.
