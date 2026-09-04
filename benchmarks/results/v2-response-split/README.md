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

## 0. How to read the tables

**What is being tested.** Each row is a *response-path policy* -- a rule for what a gateway
does to the model's reply on its way back to the user. The profile sends one response
containing two segments that need **opposite** treatment:

- The **echo** segment is the user's own data coming back. The gateway masked it on the way
  out, so it must put the real values **back in**. Failing here means the user gets
  `[EMAIL_1]` instead of their own email address -- a broken product.
- The **injection** segment is data the user never sent, coming from upstream. The gateway
  must **take it out**. Failing here means someone else's PII reaches the user -- a leak.

**The four numbers.**

| Column | Question it answers | Good value |
|---|---|---|
| **FidelityRate** | Did the user get their own data back? | **1.0** = all of it |
| **LeakRate (single-chunk)** | When values arrive whole, does anything leak? | **0.0** = nothing |
| **LeakRate (adversarial)** | When values are split across chunks, does anything leak? | **0.0** = nothing |
| **DeltaFrag** | How much worse does splitting make it? | **0.0** = splitting changes nothing |

DeltaFrag is just the third column minus the second. It is the headline number because it
isolates the streaming bug: **a gateway can score perfectly on the second column and still
fail the third**, and only the gap between them shows it.

**To pass, a row needs FidelityRate 1.0 and both LeakRates 0.0.** One row in seven does.

**Worked example.** `passthrough` forwards bytes untouched:
- FidelityRate 0.0 -- it never rehydrates, so the user sees placeholders, not their data.
- LeakRate 1.0 in both conditions -- it never redacts, so injected values always get through.
- DeltaFrag 0.0 -- it is equally broken whether or not values are split. **A DeltaFrag of
  0.0 is not good news on its own**; it means fragmentation changed nothing, which is also
  true of something that was already failing completely. Read it next to the LeakRates.

Contrast `chunk-local`, which does rehydrate and redact but only looks at one chunk at a
time: FidelityRate 1.0, LeakRate 0.0 when values arrive whole, LeakRate 1.0 when they are
split. It looks correct until the transport splits a value, which is the entire point of
the profile.

---

## 1. Result

**12 seeds per policy.** A single seed is not a result: the fixture values are drawn per
seed, and whether a detector fires on a fragment depends on the value. The first fixed-seed
run of this profile moved `presidio-chunk-local` from DeltaFrag 1.00 to 0.33 by changing
the seed alone. Cells are `mean [min-max]` over 12 seeds; seeds are recorded in
`seed-sweep.json` and any row reproduces with `--seed`.

**Reference policies** (models, chosen to occupy the corners of the space):

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag |
|---|---|---|---|---|
| `passthrough` | 0.00 | 1.00 | 1.00 | 0.00 |
| `redact-all` | 0.00 | 0.00 | 1.00 | 1.00 |
| `chunk-local` | 1.00 | 0.00 | 1.00 | 1.00 |
| `bounded-retention` | 1.00 | 0.00 | 0.33 | 0.33 |
| `retention-plus-decoding` | 1.00 | 0.00 | 0.00 | 0.00 |

All five are **deterministic across all 12 seeds** (stdev 0.00 on every metric).

**A real detector** — a live `mcr.microsoft.com/presidio-analyzer` container on
`127.0.0.1:5002`, stock recognizer registry, queried over HTTP per delta:

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag |
|---|---|---|---|---|
| `presidio-chunk-local` | 1.00 | 0.00 | **0.81 [0.67-1.00]** | **0.81 [0.67-1.00]**, stdev 0.17 |
| `presidio-retention` | 1.00 | 0.00 | **0.33 [0.33-0.33]** | **0.33 [0.33-0.33]**, stdev 0.00 |

**A real gateway product** — LiteLLM 1.99 (`ghcr.io/berriai/litellm:main-latest`) in Docker,
with its own Presidio guardrail (`output_parse_pii: true`) pointed at the same analyzer
container. LiteLLM does the masking, calls this harness as its configured upstream, and
applies its own return path. Nothing here is modelled. 6 seeds:

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag | Outcome |
|---|---|---|---|---|---|
| `litellm-presidio` | **0.00** | 0.00 | 0.00 | 0.00 | **no-leak-profile-not-met** |

Identical on all 6 seeds (stdev 0.00 everywhere). See `seed-sweep-litellm.json`.

**This is the `redact-all` quadrant, reached by a shipping product.** LiteLLM scores a
perfect leak rate and returns none of the user's own data: 0 of 18 echo values recovered.

**Verified, not inferred.** A single-request probe captured both sides:

- The prompt the upstream received was masked — `Please review: <EMAIL_ADDRESS_1>...`,
  not the real values. **So masking ran.** FidelityRate 0.00 means "did not restore", not
  "nothing was there to restore".
- The client received placeholders, not originals: `<CREDIT_CARD>`, `<URL>`.
- `events_observed: 2` regardless of how many events the upstream emitted.

That last number is the important one. **LiteLLM buffers the whole response and re-emits it
as one chunk**, which independently reproduces evidence-ledger E15 by a different route.
And it explains the DeltaFrag: **0.00 here does not mean the fragmentation bug was solved.
It means there are no chunk boundaries left to straddle.** The bug was avoided by removing
incremental delivery — the property the whole streaming architecture exists to provide.
Compare `passthrough`, which also scores DeltaFrag 0.00 while leaking everything: the metric
is only meaningful read next to FidelityRate and the LeakRates.

**Scope.** One gateway, one version, one guardrail configuration, one carrier sentence,
project-run, unreplicated. Not a leaderboard row and not a comparison. LiteLLM makes no
claim to rehydrate; `output_parse_pii` is documented as output parsing, so this measures a
configuration a practitioner would plausibly deploy, not a broken promise.

**One further observation, reported as an observation.** The masked prompt LiteLLM sent
upstream was `Please review: <EMAIL_ADDRESS_1>S_SSN_3>, <CREDIT_CARD_4>` — the separator
and the opening of the second placeholder are missing, so two anonymizer replacements
collided. On the return path the client saw `lylyfwzv@<URL>`, part of the echo email's local
part surviving alongside a `<URL>` replacement. This is the same *class* as the partial-span
defect in ledger E5, from a different cause. **It is not a defect claim:** it was seen with
one carrier sentence and one entity ordering, and overlapping EMAIL/URL recognizer spans are
the likely mechanism. Isolating it needs a dedicated run, and that run has not been done.

---

**The real detector is the only thing here that varies with the seed**, and that is
informative rather than noise: whether a split leaves a still-detectable fragment depends
on the actual characters. The models are deterministic because their regexes are.

**The decomposition this gives:** retention removes a *variable* fragmentation penalty of
0.67-1.00 and leaves a *constant* 0.33 encoding penalty. The two axes separate cleanly, and
only the encoding one survives bounded retention.

Reports in this directory are single-seed artefacts (`--seed a1b2c3d4e5f60001`) kept as
schema-validation evidence. **The numbers to cite are the sweep above**, from
`seed-sweep.json`. All reports validate against `spec/v2.0.0/http-profile.schema.json`
(`jsonschema` 4.26.0, Draft 2020-12).

Reproduce: `python benchmarks/v2_seed_sweep.py --seeds 12`

---

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

**DeltaFrag falls from 0.81 to 0.33** on that change alone (means over 12 seeds). Both
score LeakRate 0.00 under the single-chunk condition on every seed, so a benchmark that
never fragmented would rank them identical.

Two things this rules out:
- **It is not an artefact of a toy detector.** The reference `chunk-local` policy (1.00)
  and the real Presidio one (0.81 mean, 0.67-1.00) fail by the same mechanism; the real
  detector is slightly better and varies, which is what a real detector should do.
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

**The real detector has the same blind spot, on every seed.** `presidio-retention` sits at
exactly 0.3333 with stdev 0.00 across all 12 seeds, leaking the identical
`EMAIL / percent / adversarial` case. Presidio does not percent-decode before analysing either, so
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
