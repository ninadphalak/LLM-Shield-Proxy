# v2 response-split emitter -- measured run

**Run:** 2026-09-04, project-run, single machine. **Not independently reproduced.**

> ### Re-measured 2026-09-05 on a fixed instrument.
>
> *(Superseded in part -- see the round 2 box below. This directory now holds thirteen rows
> from the current instrument and twenty-four that predate it, and the build says so.)*
>
> The leak inspector was fixed for the ninth and tenth time -- a `startswith("data: ")`
> response parser, and ordered joins keyed by key name rather than by JSON path (see
> §2.7) -- and `configured_upstream_boundary`, `sse_validity` and `capture.self_probe`
> stopped being literals. **Every row in this directory was then re-run against live
> containers and a real Google Cloud project.**
>
> **Not one response-path RATE moved.** Five reference policies, both Presidio rows, all
> four Google rows, and all four gateway rows reproduce their previous rates to the digit.
> That is the evidence the fix closed blind spots rather than changing arithmetic, and it
> is the only reason the older tables below can still be read next to the new ones.
>
> **Correction, 2026-09-05, from the round 2 review of these repairs.** This paragraph
> originally said "not one response-path NUMBER moved", and that was false as written.
> Recomputed mechanically over every leaf of every artefact:
> `checks.fragmentation_safety.events_observed_max` moved in **15 of 16 rows** (the
> intended `worst`-to-`max` fix, but it is a published number and it is the `events`
> column of the tables below); `portkey-gateway-oss`'s
> `checks.response_fidelity.passed` flipped **false to true** and its denominator went
> 128 to 96, undocumented; and `nemo-guardrails`'s `checks.sse_validity.passed` went
> **true to false**. What is true, and is the claim worth making, is that
> `metrics.leak_rate.*`, `metrics.fidelity_rate` and `metrics.delta_frag` are byte-identical
> in all 16 rows.
>
> **What did change is the request path**, which no report could see while the check was a
> hardcoded pass, and **the Higress row, which was withdrawn** because its plugin config was
> not valid YAML and had never loaded. Both are covered below.
>
> **Three rows were then added**: LLM Guard 0.3.16 in two integration modes and Guardrails
> AI 0.10.2. Both are OSS libraries needing no account, and between them they state the
> whole argument out of other people's software -- the one that retains cannot restore, and
> the one that restores must choose between retaining and streaming.
>
> `tests/conformance/test_results_are_comparable.py` now fails the build when a published
> row's `inspection_scope` is not the one the current emitter generates, and when a sweep
> file carries no `instrument` block. The existing corpus guards compare case definitions
> and could not see an inspector change underneath them, which is exactly how a row scored
> by a walk with six blind spots came to sit next to one scored without them.

> ### Round 2, 2026-09-05: the repairs above were reviewed, and seven more defects found
>
> **Three of the seven were introduced BY a repair**, and one by the repair for the closest
> neighbouring defect: `_fidelity_check` was narrowed to the measurable cases to fix a
> denominator, and the narrowing made `response_fidelity.passed` vacuously **true** on
> zero of them. Full list, demonstrations and directions:
> `.llm/research/ieee-software/` review notes; regression tests in
> `tests/conformance/test_v2_round_two_repairs.py`.
>
> **13 of 37 artefacts have been re-run on the twice-fixed instrument and NOTHING MOVED**
> -- not a rate, not an outcome, not a check verdict. The seven single-run rows and the
> five `exhaustive-splits/` rows I could reach at Tier 0 and Tier 1 differ from what was
> published only by *added* fields: `instrument`, `data_events_observed`, the regenerated
> `inspection_scope`, and `leak_evidence` gaining a recovery tier. `passthrough` still
> leaks 1.00/1.00, `retention-plus-decoding` is still the only `pass`, and
> `presidio-chunk-local` under `--exhaustive-splits` is still 1.00 / DeltaFrag 0.875.
>
> **`seed-sweep.json` -- the file this README calls "the numbers to cite" -- was re-run
> in full: 7 policies x 12 seeds = 84 corpus runs, 2,688 cases. Mean, min, max and stdev
> of all four metrics: 0 of 112 summary statistics moved.** That is the strongest control
> available without a container or a billed project.
>
> **Worth reading in the re-run rows**: `leak_evidence[].observed` used to be the constant
> `normalized-match` for every leak. It now reports HOW the value was recovered, and in
> every re-run row every leak is `literal`, `same-path-join` or `single-field`. **Not one
> published leak rests on a cross-field concatenation** -- which is the tier a coincidence
> can reach, and the tier the round 7 IPv4 false positive lived in.
>
> **24 artefacts are STALE and `test_every_artefact_records_the_instrument_that_produced_it`
> is RED because of them**, correctly. They are the four Google rows, the six external
> gateway rows (LiteLLM, Portkey, NeMo, both Shield configs, plus LLM Guard ×2 and
> Guardrails), the four Google `exhaustive-splits/` rows, and the eight per-target
> `seed-sweep-<target>.json` files that cover them. Each needs a container, a billed cloud
> project or a separate 3.12 venv. **Do not read them beside the thirteen fresh rows, and
> do not weaken the guard to make the build green.** Re-run them or move them out.

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

**All 19 single-run artefacts in this directory come from one corpus: 32 cases, 5 axes,
76/76 pairs.** `tests/conformance/test_results_are_comparable.py` fails the build if that
stops being true, because on 2026-09-04 this directory briefly held artefacts from four
different corpus generations at once -- individually correct, jointly misleading. A stale
row is worse than a missing one: a missing row is visibly absent, a stale one looks like
evidence.

The `seed-sweep-*.json` files are the numbers to cite; the single-run artefacts are kept as
schema-validation evidence.

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
published here before 2026-09-04.** Adding `request_site` and then the `USPHONE` entity took
a run from 6 cases to 32, so the denominators moved twice: `bounded-retention` reads **0.125**
because it leaks two cases out of sixteen adversarial ones -- it was 0.33 (one of three) on the
four-axis corpus and 0.25 (one of four) on the first five-axis one. **The same case leaks.
Nothing about any policy changed.** (The 0.25/0.33 wording here survived the entity-axis
change and was corrected 2026-09-05.)

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

**Correction (2026-09-05):** this said "identical on all 6 seeds (stdev 0.00 everywhere)"
and `seed-sweep-litellm.json` does not support it. Across the six seeds LiteLLM's leak rate
is **mean 0.06, range 0.00-0.19, stdev 0.097** in both conditions; two of the six seeds leak
and four do not. The single-seed `0.00` above is one draw, not the distribution. Only
FidelityRate and DeltaFrag are actually constant at 0.00.

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

| Gateway | Fidelity | Leak (1-chunk) | Leak (adv) | DeltaFrag | Req-path config | **Req-path egress** | Outcome |
|---|---|---|---|---|---|---|---|
| `litellm-presidio` (LiteLLM 1.99) | 0.00 | 0.06 [0.00-0.19] | 0.06 [0.00-0.19] | 0.00 | **configured** | **all 4** | `fail` |
| `llm-shield-proxy-1.6.0` response redaction **off** | **1.00** | **1.00** | **1.00** | 0.00 | configured | none | `fail` |
| `llm-shield-proxy-1.6.0` response redaction **on** | **1.00** | **0.12** | **0.25** | 0.12 | configured | none | `fail` |
| `portkey-gateway-oss` | **1.00** | **1.00** | **1.00** | 0.00 | not configured | all 4 | `fail` |
| `nemo-guardrails-0.24.0` | 0.00 | 0.06 [0.00-0.17] | 0.06 [0.00-0.17] | 0.00 | not configured | all 4 | `fail` |

### The request-path columns are new, and only one of the three positives is a finding

**Added 2026-09-05.** `checks.configured_upstream_boundary` used to be a hardcoded pass, so
the request path was reported clean for every gateway ever measured and `outcome` was
derived from the response path alone. It is now measured: every body the capture recorded,
across all 32 cases, walked and matched the same way the client-side inspector works, with
the provenance the schema demands (`channel`, `scope`, `match`) rather than a bare entity
name. All three positives are **`match: literal`, `scope: per-request`** -- the raw value,
verbatim, inside one body the gateway sent. Not a normalized coincidence.

**Read the two request-path columns together, and read the config one first.**
`redaction_claim.request_path_redaction_configured` exists because the egress column alone
would repeat this file's own recorded mistake -- measuring the config in this repository
and publishing it as a property of a product:

- **Portkey and NeMo were configured with response-side guardrails only** (Portkey with an
  `output_guardrails` header, NeMo with `detect sensitive data on output`). Neither was ever
  asked to mask the request. Their egress is a consequence of how this harness configured
  them, **not a coverage defect**, and must not be reported as one. Both still fail on the
  response path, which is what their rows are about.
- **LiteLLM was configured to mask the request** -- `mode: pre_call` in
  `../../litellm-v2-profile/config.docker.yaml` -- and egressed all four anyway. That one is
  a finding about the product.

`outcome` is `fail` for all three, because a measured egress of a protected value is what
v1 reserves `fail` for regardless of intent. The attribution field is how a reader tells the
three apart, and it is why the field had to exist before this table could be published.

**LiteLLM's is the finding, and it is site-specific.** Its Presidio guardrail masks
`messages[*].content` correctly. Measured per site, same run, same fixture:

| `request_site` | reached the upstream |
|---|---|
| `chat-content` | masked (`<EMAIL_ADDRESS_1>` etc.) |
| `system-content` | masked |
| `unrecognised-key` | **EMAIL, SSN, CARDPAN, USPHONE verbatim** |
| `tool-description` | **EMAIL, SSN, CARDPAN, USPHONE verbatim** |

So the finding is scoped: **the guardrail covers the chat message shapes it knows by name,
and the same text in a tool description or an unrecognised top-level key goes upstream
untouched.** That is exactly the blindness the `request_site` axis was added to expose, and
for four months nothing could report it because the check that would have was a constant.

**And it is what finally separates the two identical rows.** `llm-shield-proxy-1.6.0`
response-off and `portkey-gateway-oss` are still identical on all four response metrics
(1.00 / 1.00 / 1.00 / 0.00). They are no longer identical in the report: Shield masks the
request path and Portkey does not. Until now that distinction lived only in prose here.

### Two OSS libraries, added 2026-09-05, and together they are the argument

LLM Guard 0.3.16 and Guardrails AI 0.10.2 are libraries rather than gateways, wrapped in the
thinnest possible gateway the same way the `presidio-*` rows wrap the analyzer. Neither
needs an account. Both need their own Python 3.10-3.12 environment, so **"free" is not the
same as "light"**: LLM Guard pins `torch>=2.4.0` and `transformers==4.51.3`.

| policy | restores? | retains? | Fidelity | Leak 1-chunk | Leak adv | DeltaFrag | events |
|---|---|---|---:|---:|---:|---:|---:|
| `bounded-retention` (reference model) | yes | `L = N-1` | 1.00 | 0.125 | 0.125 | **0.00** | 6 |
| `guardrails-ai-stream-validate` | **no such API** | to next sentence | 0.00 | 0.125 | 0.125 | **0.00** | 6 |
| `llm-guard-chunk-local` | **yes** | no | **1.00** | 0.17 [0.00-0.25] | 0.75 | **0.58 [0.50-0.75]** | 5 |
| `llm-guard-buffered` | **yes** | whole response | 1.00 [0.98-1.00] | 0.29 [0.25-0.31] | 0.29 [0.25-0.31] | **0.00** | **3** |

**Guardrails AI reproduces `bounded-retention` exactly.** Same leak rates, same DeltaFrag,
same event count, and the same residual case (`EMAIL / percent / adversarial`, an encoding
gap rather than a fragmentation one). Its `Validator.validate_stream` accumulates chunks to
the next sentence boundary before validating -- retention across chunk boundaries, shipping,
in OSS, from a project with no knowledge of this profile. **That is the strongest external
corroboration of the retention result in this repository.**

**And it cannot restore.** There is no `reidentify`, `deanonymize` or `unredact` in its 178
modules. Its FidelityRate 0.00 is a real failure and not a vacuous one: the request is
forwarded unmasked, so the echo segment returns carrying the caller's own values, and the
validator redacts them -- because nothing tells it whose data it is. **That is the response
split's central claim demonstrated by a product instead of by a model.** It is not scored
for failing to rehydrate; it is scored for what one global policy does to two segments that
need opposite treatment.

**LLM Guard restores, and it is the first third-party product here to do so.** FidelityRate
1.00 from `Anonymize` + `Vault` + `Deanonymize`. Applied per delta it pays **DeltaFrag
0.50** -- the same scanner on the same corpus leaks three times as often when the value is
split (DeltaFrag 0.58 [0.50-0.75] over six seeds). Buffering the whole response removes
that penalty entirely (to 0.00 on every seed) and takes
`events_observed` from 5 to 3. **E15, reproduced a third time, on the product that gets
everything else right.**

Two more observations, both about defaults and neither a defect report:

- **`Sensitive(redact: bool = False)`.** On the default LLM Guard's output scanner detects,
  logs `Found sensitive data in the output`, returns `is_valid=False` -- and returns the
  text **unchanged**. Verified before any row was run. `gateway.py` passes `redact=True`; a
  row on the default would have measured this repository's wrapper.
- **The two request-path failures are different in kind, and the axes separate them.**
  LiteLLM leaks **all four entities at two of four sites** -- a payload-walk gap. LLM Guard
  leaks **one entity (USPHONE) at all four sites** -- a detector-coverage gap. `request_site`
  finds the first; `detector_blind_entities` finds the second. Neither axis finds both.

**Two of these rows were wrong until the configuration was fixed, and both errors were
mine rather than the gateway's.** Adding the `USPHONE` entity made NeMo leak 0.17 and made
Google Cloud DLP look phone-blind. NeMo's config named three entities and not the fourth;
the DLP wrapper asked for three infoTypes and not the fourth. **A row that measures the
harness operator's config file is not a result about the product**, so both were corrected
and re-run before publishing. NeMo went 0.17 to 0.06 on the fix.

**Corrected 2026-09-05. Two of these four are NOT deterministic, and the intervals in the
table above are the reason the point estimates must not be quoted alone.** Recomputed from
the committed sweeps:

| Row | leak, 6 seeds | stdev |
|---|---|---:|
| `portkey-gateway-oss` | 1.00 on every seed | 0.000 |
| `llm-shield-proxy-1.6.0` off / on | 1.00 / 0.125 on every seed | 0.000 |
| `litellm-presidio` | mean 0.06, range 0.00-0.19 | **0.097** |
| `nemo-guardrails-0.24.0` | mean 0.06, range 0.00-0.17 | **0.086** |

See `seed-sweep-litellm.json`, `seed-sweep-shield-160-off.json`,
`seed-sweep-shield-160-on.json`, `seed-sweep-portkey.json`, `seed-sweep-nemo.json`.
(`seed-sweep-shield-152.json` was cited here and has never existed; the 1.5.2 sweep was
superseded by the two 1.6.0 files before it was committed.)

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

**The real detector has the same blind spot.** `presidio-retention` leaks the
`EMAIL / percent / adversarial` case on every seed. **Corrected 2026-09-05:** this said
"exactly 0.3333 with stdev 0.00 across all 12 seeds", which was a three-entity number left
behind when the corpus went to four. `seed-sweep.json` gives mean **0.1615**, range
0.125-0.375 -- it is the *identical* case every time, and the rate varies only because
Presidio occasionally catches a second one. Presidio does not percent-decode before analysing either, so
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

`corpus.coverage.axes` requires all five axes --
`entity, encoding, fragmentation, carrier, request_site`. A single-axis sweep cannot
produce a valid v2 report. That is the schema working as designed, and it is why this
emitter carries a real pairwise covering array rather than a fragmentation-only sweep.

Generated array: **32 cases, 76 of 76 pairs covered, `proof_complete: true`**, recomputed
from the emitted cases rather than asserted. (This section said "6 cases, 30 of 30" until
2026-09-05 -- a four-axis, three-entity number that outlived two corpus extensions.)

Verified independently of the report: the array is **twinned**, so DeltaFrag is a
within-case difference. 16 cases in each fragmentation condition, identical populations on
the other four axes, no duplicates. A greedy pairwise array alone gave 8 against 4 and
DeltaFrag was then attributing a composition difference to fragmentation.

---

### 2.7 The instrument had two more false passes, and both flattered the target

Found 2026-09-05 by an adversarial review told to assume the instrument was wrong.
Ninth and tenth in this series; both demonstrated end to end before being fixed.

**(a) The response parser was a `startswith("data: ")` test.** Every other byte of the
response was discarded. `data:` *without* the space is legal SSE -- WHATWG HTML 9.2.6 says
"if the value starts with a U+0020 SPACE, remove it", so the space is optional, not
required. A multi-line `data` payload is one event joined with U+000A, not several. And a
gateway may answer a 200 that is not a stream at all. Measured: a relay that redacted
nothing and re-emitted with `data:` scored **LeakRate 0.00/0.00**; the identical relay
with `data: ` scored 1.00/1.00. Replaced with a real parser (`_parse_sse`). Everything it
does not dispatch -- comments, unknown fields, an unterminated trailing event -- is still
scanned, because the spec tells a *client* to ignore those, not an *inspector*.

**(b) The ordered joins were keyed by key NAME.** `delta.content` had a stream,
"everything else" had another, and any key called `content` or `text`, at any depth, was
skipped from the second to keep it out of the first. So a value split across two events
under `delta.raw.text` was in neither ordered stream -- and the fallback join interleaves
object keys between the halves, so it did not reassemble there either. **The same policy
was run twice, changing nothing but the JSON key its text came out under:**

| policy | envelope | Fidelity | Leak (1-chunk) | Leak (adv) | DeltaFrag | Outcome |
|---|---|---:|---:|---:|---:|---|
| `chunk-local` as published | `delta.content` | 1.00 | 0.125 | 1.00 | **0.875** | `fail` |
| `chunk-local` as published | `delta.raw.text` | 1.00 | 0.125 | **0.00** | **-0.125** | `fail` |
| `chunk-local` + percent-decoding | `delta.content` | 1.00 | 0.00 | 1.00 | **1.00** | `fail` |
| `chunk-local` + percent-decoding | `delta.raw.text` | 1.00 | 0.00 | **0.00** | **0.00** | **`pass`** |

The last two rows are the full false pass, and the fourth row is why the third exists:
`chunk-local` as published leaks the percent-encoded case even unfragmented, so its
`leak_single` of 0.125 keeps the outcome at `fail` no matter what the envelope hides. Give
it a detector that also percent-decodes -- still chunk-local, still holding no state across
deltas, which is exactly the defect this profile exists to measure -- and the nested
envelope takes it to a clean `pass`. `delivery_confirmed: true` and
`detector_blind: []` in every row; the needle arrived in two consecutive events and
reassembled the way any client reassembles a field.

**The corpus escaped only because its own sibling carrier happens to be called
`record_field`.** Channels are now keyed by JSON path, so two fragments join if and only if
they arrived the same way, and no key name is special.

**Also closed in the same pass**, all of them claims the code did not implement:

- `configured_upstream_boundary` was a **literal**. It never opened `upstream_bodies`. A
  relay forwarding all four protected values verbatim to the capture was certified
  `passed: true, leaked_entity_types: []` under sixty words of recursive-decoding prose.
  It now inspects every captured body across every case, and its scope sentence is
  generated from a capability registry with a test per clause -- the same mechanism the
  client-side scope already had, applied to the half that had been left behind.
- `sse_validity` was a literal: `content_type_valid: true, status_codes: [200],
  invalid_events: 0, errors: []`, emitted by a run whose gateway answered
  `application/json` with no events.
- `events_observed_max` was a **minimum**: both event fields were read off the leaking
  case with the *fewest* events. `chunk-local` runs 16 cases at 4 events and 16 at 5, and
  the report said max 4.
- `fragmentation_strategy` was the constant `"exhaustive-2-part"` while the code cut once
  at the value midpoint and `limitations.method_limits` in the same report said "not
  every split point". The label is now derived from the splits actually run, and
  `--exhaustive-splits` makes it true (see §3).
- `limitations.method_limits` said "Three entity types" for as long as there have been
  four. It is derived from `AXES` now.

**Still fabricated, and not fixed here:** `capture.self_probe` reports
`performed: true, recorded: true, round_trip_ms: 0.0` and no self-probe is performed;
`target.base_url`, `target.model`, `capture.port` and `capture.authentication_required`
are hardcoded to the in-process defaults in every external-gateway row, and
`redaction_claim.claim_citation` cites this emitter's own policy docstrings as the source
of a third-party vendor's redaction claim.

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

### 3.1 The midpoint split is one sample. Take all of them.

Default `fragmentation: adversarial` cuts the value once, at its midpoint. That is one
draw from the thing being measured, and whether a split defeats a detector depends on what
the two halves *look like*, not on where the middle is -- which is how a fragment can match
for an unrelated reason, suppress the leak, and score the fragmented condition as safe
(the negative DeltaFrag in §1).

**Sampling is the wrong instinct here, because the space is tiny.** A value of N characters
has exactly N−1 internal two-part splits: about 20 for an email, 11 for an SSN. Enumerating
it is both cheaper and strictly stronger than drawing from it, which is the bounded
exhaustive testing tradition rather than fuzzing. `benchmarks/presidio_partition_probe.py`
already applies this oracle to a stock Presidio (47 split points, none of which protect the
value); `--exhaustive-splits` brings it to the scored corpus:

```bash
python -m pii_leak_benchmark.v2_emitter --only chunk-local --exhaustive-splits \
  --out /tmp/exhaustive
```

A case then leaks if **any** of its split points leaks, and the report says which oracle
ran: `fragmentation_strategy` is `across-sse-events` for the midpoint and
`exhaustive-2-part` for the full enumeration, with the split count in
`limitations.method_limits`.

**Measured on every row it could be, seed `a1b2c3d4e5f60001`, 252 splits over 16
adversarial cases:**

| Policy | midpoint adv / DeltaFrag | exhaustive adv / DeltaFrag |
|---|---|---|
| `chunk-local` | 1.00 / 0.875 | 1.00 / 0.875 |
| `bounded-retention` | 0.125 / 0.00 | 0.125 / 0.00 |
| `retention-plus-decoding` | 0.00 / 0.00 | 0.00 / 0.00 |
| **`presidio-chunk-local`** | 0.50 / 0.375 | **1.00 / 0.875** |
| `presidio-retention` | 0.125 / 0.00 | 0.125 / 0.00 |
| `gcp-dlp-chunk-local` | 1.00 / 0.50 | 1.00 / 0.50 |
| `gcp-dlp-retention` | 0.50 / 0.00 | 0.50 / 0.00 |
| `gcp-model-armor-chunk-local` | 1.00 / 0.25 | 1.00 / 0.25 |
| `gcp-model-armor-retention` | 0.75 / 0.00 | 0.75 / 0.00 |

**One row moves, and it is the one that should.** `presidio-chunk-local` nearly doubles:
LeakRate(adversarial) 0.50 to **1.00**, DeltaFrag 0.375 to **0.875**. Every adversarial case
leaks at *some* split point; the midpoint happened to land on cuts Presidio still caught in
half of them. **The midpoint was under-reporting a real detector's fragmentation failure by
half**, and that number was published.

**Everything else is unchanged, which is the more valuable half of the result.** The three
modelled policies are deterministic regexes with no per-fragment behaviour, so the midpoint
is a sufficient statistic for them. And the retention rows -- `bounded-retention`,
`presidio-retention`, `gcp-dlp-retention` -- hold their DeltaFrag at exactly 0.00 across
**every internal split point of every value**, not merely at the one the midpoint picked.
Since the same oracle demonstrably moves `presidio-chunk-local`, that is a real result and
not an insensitive instrument: **bounded retention is not merely surviving the sample, it is
surviving the enumeration.**

**Practical rule: do not quote a DeltaFrag from a context-scored or validating detector
without `--exhaustive-splits`.** Presidio, Cloud DLP and Model Armor all score a fragment on
what it looks like, so where you cut changes the answer. A regex does not.

**What this axis still does not do:** it fragments the VALUE across SSE events. It does not
fragment the SSE framing itself -- a `data:` line cut across TCP segments, a `\r\n` split
between chunks. That is not an oversight and it is not measurable from here: the WHATWG
byte-stream parser is defined to reassemble across arbitrary byte boundaries, so a
conformant gateway is immune to it by construction, and this harness does not control where
the gateway's own client chunks its reads. Value-level fragmentation across events is an
application-protocol property, and it is the one no spec makes safe.

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
