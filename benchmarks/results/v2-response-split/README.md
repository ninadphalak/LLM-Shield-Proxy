# v2 response-split emitter -- measured run

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
| `redact-all` | 0.00 | 0.12 | 1.00 | 0.88 |
| `chunk-local` | 1.00 | 0.12 | 1.00 | 0.88 |
| `bounded-retention` | 1.00 | 0.12 | 0.12 | 0.00 |
| `retention-plus-decoding` | 1.00 | 0.00 | 0.00 | **0.00 -- the only `pass`** |

All five are **deterministic across all 12 seeds** (stdev 0.00 on every metric).

**These are five-axis numbers and they are not comparable case-for-case with anything
published here before 2026-09-04.** Adding `request_site` took a run from 6 cases to 12, so
the denominators moved: `bounded-retention` reads 0.25 rather than 0.33 because it leaks one
case out of four adversarial cases instead of one out of three. **The same single case leaks.
Nothing about any policy changed.**

**A real detector** -- a live `mcr.microsoft.com/presidio-analyzer` container on
`127.0.0.1:5002`, stock recognizer registry, queried over HTTP per delta:

| Policy | FidelityRate | LeakRate (single-chunk) | LeakRate (adversarial) | DeltaFrag |
|---|---|---|---|---|
| `presidio-chunk-local` | 1.00 | 0.17 [0.12-0.38] | **0.72 [0.25-1.00]** | **0.55 [-0.12-0.88]** |
| `presidio-retention` | 1.00 | 0.16 [0.12-0.38] | **0.16 [0.12-0.38]** | **0.00** |

### A negative DeltaFrag survives the pairing fix, and it is real

`presidio-chunk-local` reaches **-0.12 on one seed of twelve**: the value leaked more when
NOT fragmented. Pairing the covering array removed the systematic cause (unequal
populations). What is left has a different and more interesting one, measured on seed
`0000000000000001`, entity `USPHONE`, value `590-555-0126`:

```
"Reference record: 590-555-0126"  ->  not detected, the value leaks
"590-55"                          ->  not detected
"5-0126"                          ->  [REDACTED]
```

Presidio misses the whole number in its carrier sentence, and matches the **fragment**
`5-0126` for some unrelated reason. Split, the reassembled client text no longer contains
the complete needle, so the case scores as "did not leak". **The fragmented condition
passed by accident, on a false positive.**

So `DeltaFrag < 0` must never be read as "fragmentation is safe here". Read it beside
`LeakRate(single_chunk)` and `detector_blind_entities`: on that seed USPHONE is
detector-blind, which is the signal that the baseline, not the fragmentation, is what
moved.

**A real gateway product** -- LiteLLM 1.99 (`ghcr.io/berriai/litellm:main-latest`) in Docker,
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

- The prompt the upstream received was masked -- `Please review: <EMAIL_ADDRESS_1>...`,
  not the real values. **So masking ran.** FidelityRate 0.00 means "did not restore", not
  "nothing was there to restore".
- The client received placeholders, not originals: `<CREDIT_CARD>`, `<URL>`.
- `events_observed: 2` regardless of how many events the upstream emitted.

That last number is the important one. **LiteLLM buffers the whole response and re-emits it
as one chunk**, which independently reproduces evidence-ledger E15 by a different route.
And it explains the DeltaFrag: **0.00 here does not mean the fragmentation bug was solved.
It means there are no chunk boundaries left to straddle.** The bug was avoided by removing
incremental delivery -- the property the whole streaming architecture exists to provide.
Compare `passthrough`, which also scores DeltaFrag 0.00 while leaking everything: the metric
is only meaningful read next to FidelityRate and the LeakRates.

**Two more shipping gateways, same harness, same capture.**

**4 entities, 32 cases, 6 seeds each. These supersede every earlier table here.**

| Gateway | Fidelity | Leak (1-chunk) | Leak (adv) | DeltaFrag |
|---|---|---|---|---|
| `litellm-presidio` (LiteLLM 1.99) | 0.00 | 0.06 [0.00-0.19] | 0.06 [0.00-0.19] | 0.00 |
| `llm-shield-proxy-1.6.0` response redaction **off** | **1.00** | **1.00** | **1.00** | 0.00 |
| `llm-shield-proxy-1.6.0` response redaction **on** | **1.00** | **0.12** | **0.25** | 0.12 |
| `portkey-gateway-oss` | **1.00** | **1.00** | **1.00** | 0.00 |
| `nemo-guardrails-0.24.0` | 0.00 | 0.06 [0.00-0.17] | 0.06 [0.00-0.17] | 0.00 |

**Two of these rows were wrong until the configuration was fixed, and both errors were
mine rather than the gateway's.** Adding the `USPHONE` entity made NeMo leak 0.17 and made
Google Cloud DLP look phone-blind. NeMo's config named three entities and not the fourth;
the DLP wrapper asked for three infoTypes and not the fourth. **A row that measures the
harness operator's config file is not a result about the product**, so both were corrected
and re-run before publishing. NeMo went 0.17 to 0.06 on the fix.

All four are deterministic across 6 seeds (stdev 0.00 on every metric). See
`seed-sweep-litellm.json`, `seed-sweep-shield-152.json`, `seed-sweep-portkey.json`,
`seed-sweep-nemo.json`.

**Read the last two columns before the first four.** "Echo observable" is the denominator
behind FidelityRate: a gateway that never forwarded the field had nothing to restore, so
`0.00` there means **not measured**, not **failed**. "Inconclusive" counts cases the gateway
refused outright. Both exist because both happened -- Portkey silently drops an unrecognised
top-level key and returns 200; NeMo answers 422 to one of the four request sites. Those are
different behaviours and the profile records them differently.

**NeMo is measured with `detect sensitive data on output`, not `mask`.** The mask rail is
unusable in 0.24.0: `mask_sensitive_data() got an unexpected keyword argument 'context'` on
every request, because its signature omits the `**kwargs` its sibling `detect_sensitive_data`
has. Verified with `inspect.signature` against the installed package. See
`../../nemo-v2-profile/STATUS.md`, which also records that NeMo refuses to stream at all when
a response guardrail is configured unless streaming rails are explicitly enabled, and that its
config validator will not allow a **rewriting** output rail to keep a retention window --
`context_size` must be `0`, which is exactly the `chunk-local` policy this profile shows
leaking under fragmentation.

### The two identical rows are not the same gateway

LLM-Shield-Proxy and Portkey score **identically on every response-path metric**, and they
are doing opposite things. The response metrics cannot tell them apart. What the upstream
received can, and the harness records it:

| Gateway | prompt the upstream actually received |
|---|---|
| client sent | `Please review: lylyfwzv@example.com, 950-36-9596, 6011-1111-1111-1117` |
| `portkey-gateway-oss` | `Please review: lylyfwzv@example.com, 950-36-9596, 6011-1111-1111-1117` |
| `llm-shield-proxy-1.5.2` | `Please review: zmaxwell@example.com, 437-77-8683, 4989557827846679218` |
| `litellm-presidio` | `Please review: <EMAIL_ADDRESS_1>S_SSN_3>, <CREDIT_CARD_4>` |

Portkey forwarded the caller's real values to the upstream. LLM-Shield-Proxy substituted
synthetic surrogates; LiteLLM substituted placeholders. **Portkey's FidelityRate of 1.00 is
vacuous** -- nothing was ever masked, so "the originals came back" is not restoration, it is
the absence of any transformation at all.

**This is a finding about the metric, not only about the gateways.** FidelityRate and
LeakRate are response-path measurements, and on their own they rank a gateway that does
nothing equal to one that protects the request path completely. A profile that reports them
without also recording what the upstream received is rankable in the wrong direction. The
v2 reports carry `capture.upstream_bodies` for exactly this reason, and any leaderboard
built on this profile has to publish that column too.

### LLM-Shield-Proxy 1.5.2

Installed **from PyPI, not built from this working tree** (`pip install
llm-shield-proxy==1.5.2` inside `benchmarks/shield-v2-profile/Dockerfile.pypi`), so the
measurement is of the released artefact. `UPSTREAM_BASE_URL` points at this harness's
capture; default masking mode (`SYNTHETIC`), Tier-3 NER off, so only structured entities
are in scope -- which is all the v2 corpus uses.

**1.5.2 exists because of this measurement.** Profiling the published 1.5.1 found that
every proxied request returned 500: `security/identity.py` imports `jwt` at module scope,
`api/main.py` imports it from inside the request handler without gating, and PyJWT was
absent from that wheel's `requires_dist`. A working-tree `docker build` installs
`requirements.txt`, which has always been a superset of the wheel's declared dependencies,
so it would have reported a clean gateway. 1.5.1 was scored with PyJWT added as a declared
deviation; **1.5.2 needs none and scores identically**, which is the evidence that the fix
was packaging-only. Details in `../../shield-v2-profile/STATUS.md`.

- **FidelityRate 1.00.** All 18 echo values across all 6 seeds were restored exactly. The
  request/response vault round-trip works, and it works through a 5-event stream: unlike
  LiteLLM, incremental delivery is preserved.
- **LeakRate 1.00, in both conditions.** Every injected value reached the client.

This is the `passthrough` corner of the *response* space reached from the opposite side of
the *request* space, and it is architectural rather than a misconfiguration: 1.5.2 has no
setting for response-side detection. `grep`ping the released `core/config.py` for a
response-scanning option returns nothing; the response path rehydrates vault placeholders,
applies the canary tripwire and the watermark, and forwards everything else. The proxy
makes no claim to redact model-originated PII, so this is a scope statement about the
product, not a broken promise -- but it is also why it cannot pass a profile that requires
both halves.

**DeltaFrag 0.00 here means the value leaks in every condition**, so fragmentation adds
nothing. Compare `redact-all`, which scores DeltaFrag 1.00 by leaking only when
fragmented. The metric is a difference and it is zero at both extremes.

### Portkey OSS gateway

`portkeyai/gateway:latest`, configured with an `output_guardrails` check of
`portkey.pii` with `redact: true`, passed through `x-portkey-config`.

**The guardrail did nothing, and said nothing.** Byte-identical output with and without the
config header, HTTP 200 either way, no error surfaced to the client. Reading the shipped
bundle explains it: all six PII checks in the OSS distribution -- under the `qualifire`,
`portkey`, `patronus`, `pangea`, `promptfoo` and `azure` namespaces -- are call-outs to a
third-party service requiring `credentials.apiKey`, and `executeHooks` catches its own
errors and returns `shouldDeny: false`. **The OSS gateway ships no local PII redaction**,
and an unconfigured or failing remote guardrail fails open silently.

That is worth stating carefully: it is a property of the open-source distribution run
without third-party credentials, which is the configuration a practitioner gets by default.
It says nothing about Portkey's hosted product or about the guardrail vendors themselves.

**Scope, for all four gateways.** One version each, one guardrail configuration each,
one carrier sentence, project-run, unreplicated, all against a synthetic capture rather
than a live model. **Not a leaderboard and not a ranking.** Each gateway is measured in the
configuration a practitioner would plausibly deploy, and each is doing roughly what its
documentation says it does -- LiteLLM makes no claim to rehydrate, LLM-Shield-Proxy makes
no claim to redact model-originated PII, and Portkey documents its PII guardrails as
integrations with named third-party services. What the profile shows is that **none of the
four satisfies both halves of the response split**, which is a statement about the
category, not about any one product's honesty. NeMo Guardrails is measured with its detect
rail because its mask rail does not run at all in 0.24.0, which is declared beside the row
rather than folded into it.

**One further observation, reported as an observation.** The masked prompt LiteLLM sent
upstream was `Please review: <EMAIL_ADDRESS_1>S_SSN_3>, <CREDIT_CARD_4>` -- the separator
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

Reproduce the reference and Presidio rows:
`python benchmarks/v2_seed_sweep.py --seeds 12`

Reproduce the gateway rows (each needs its container up; see
`benchmarks/shield-v2-profile/` and `benchmarks/litellm-v2-profile/` for the exact
`docker run` lines):

```bash
# LLM-Shield-Proxy 1.5.2, installed from PyPI inside the image
docker build -f benchmarks/shield-v2-profile/Dockerfile.pypi \
  -t shield-pypi:1.5.2 benchmarks/shield-v2-profile

V2_GATEWAY_TOKEN=sk-shield-v2-profile python benchmarks/v2_seed_sweep.py --seeds 6 \
  --only llm-shield-proxy-1.5.2 \
  --gateway-url http://127.0.0.1:8811/v1/chat/completions \
  --upstream-port 8799 --model capture \
  --out benchmarks/results/v2-response-split/seed-sweep-shield-152.json

# Portkey OSS. Routing and guardrail config travel as headers, not in the URL, so they
# go through V2_GATEWAY_HEADERS. Keep it on one line: the value is JSON inside JSON.
export V2_GATEWAY_HEADERS='{"x-portkey-provider":"openai","x-portkey-custom-host":"http://host.docker.internal:8799/v1","x-portkey-config":"{\"output_guardrails\":[{\"checks\":[{\"id\":\"portkey.pii\",\"parameters\":{\"redact\":true}}]}]}"}'

V2_GATEWAY_TOKEN=sk-dummy python benchmarks/v2_seed_sweep.py --seeds 6 \
  --only portkey-gateway-oss \
  --gateway-url http://127.0.0.1:8788/v1/chat/completions \
  --upstream-port 8799 --model capture \
  --out benchmarks/results/v2-response-split/seed-sweep-portkey.json
```

`benchmarks/shield-v2-profile/probe_gateway.py --url ... --token ...` prints both sides of
a single request for any of them, which is how you check that a FidelityRate of 0.00 means
"did not restore" and not "nothing was there to restore"

---

## 2. The six findings a reviewer should check

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
single-chunk condition** -- all three score LeakRate 0.0 and FidelityRate 1.0. A benchmark
that only sent whole values inside single chunks would rank them equal.

Under the adversarial condition they separate: 1.0, 0.3333, 0.0. DeltaFrag is exactly that
gap, and it is the number that reports how much of a gateway's apparent correctness is an
artefact of being tested on unfragmented input.

### 2.3 The same result holds for a real detector, not just the models

`presidio-chunk-local` and `presidio-retention` use a **live Presidio analyzer** -- same
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

### 2.6 The instrument had a capture-side false pass, and it flattered the target

Found while measuring Portkey, fixed in this commit, and recorded because a leak
instrument that fails toward "secure" is the one failure it must never have.

An external-gateway run rebinds the capture to the **same fixed port** for every case. The
old teardown called `shutdown()` only -- which ends the accept loop but leaves the socket
bound and leaves live handler threads running. A gateway that pools connections held a
keep-alive socket across the case boundary, the predecessor's thread answered, and the case
was scored against the **previous case's** injected values.

The observed symptom: Portkey, a gateway that redacts nothing at all, scored **LeakRate
0.33** -- because in the SSN and CARDPAN cases the client received the EMAIL case's
response, the SSN needle was correctly absent from it, and "needle absent" was scored as
"did not leak". The true value is 1.00.

The fix is `Connection: close` from the capture plus `server_close()` on teardown, so no
socket can outlive the fixture it was opened against.
`tests/conformance/test_v2_capture_isolation.py` fails without it.

**Everything already published was re-measured after the fix, and nothing moved.** LiteLLM:
0.00 on every metric, 6 seeds. The full 12-seed reference and Presidio sweep reproduced
identically, `presidio-chunk-local` included at 0.81 [0.67-1.00]. Only external-gateway runs
could ever have been affected, because in-process runs bind a fresh ephemeral port per case
and so had no socket to reuse. No number in this file predates the fix.

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

**Adversarial checks worth running** -- each targets a way this result could be hollow:

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
   -- it forwards the *masked* prompt, so a policy that does nothing must fail fidelity. If
   passthrough ever scores 1.0, the upstream is echoing unmasked text and the measurement
   is void.
5. **Does the client inspector over-reach?** `_present` normalizes and percent-decodes. Confirm
   it does not fold ASCII in a way that manufactures matches -- the v1 harness has a
   documented false-positive incident from exactly that.

---

## 4. Limits -- stated in the reports and repeated here

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
- The `seed` field is recorded but **values are not reproducible from it** -- the fixture
  generator is not seeded. This is a real gap against the schema's intent, which is that a
  seed reproduces the drawn values. Recorded rather than papered over.

## 5. What this does not do

**The `presidio-*` rows are still models.** They measure a real, widely deployed *detector*
inside a wrapper written here; the rehydration half is the wrapper's. Only the three
gateway rows measure a vendor's own shipped streaming path end to end.

**It is not a leaderboard.** Four gateways, one version each, one configuration each, one
carrier sentence, run once by the author of one of them. A leaderboard needs replication,
version ranges, more than one carrier, and someone other than this project running it.

**The capture is not a model.** The upstream is a synthetic SSE emitter, so the injection
segment is what the harness chose to inject, not what a model would actually say. That is
deliberate -- it is what makes the injected values known ground truth -- but it means the
LeakRates are conditional on an injection pattern, not on observed model output.

**The corpus only uses `messages[0].content`.** Every scored case puts its protected value
in one chat field, so the profile says nothing about the rest of a request body -- and a
real MCP or JSON-RPC caller carries values in `system`, in tool arguments, in nested
metadata, in keys no schema names. `benchmarks/shield-v2-profile/probe_json_fields.py`
checks that separately for LLM-Shield-Proxy and finds the deep walk masks all of them
(leaving structural keys like `tools[0].function.name` alone, correctly) and that
rehydration is keyed by value rather than by field. **That is a probe, not a scored row**,
and it has been run against one gateway only. A carrier axis for non-chat JSON fields is
the principled fix and has not been built.

**Not yet done:** a dedicated run isolating the LiteLLM/Presidio placeholder collision; a
run of LLM-Shield-Proxy with `SHIELD_DEFAULT_MASKING_MODE` other than `SYNTHETIC`; any
gateway with a *local* response-side PII detector, which none of the three has.
