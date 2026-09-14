---
sidebar_position: 8
title: Benchmark revision history
---

# Benchmark revision history

The chronological record of how the v2 response-split instrument was corrected, kept
because the corrections are the strongest evidence about the instrument's reliability.

**Ten inspector defects have been found and fixed in this harness, and every one of them
flattered the target.** A leak instrument that fails toward "secure" is the one failure
mode it must never have. That is why this page exists rather than being deleted after each
fix.

Nothing on this page is current-state documentation. For what the profile measures now, see
the [benchmark readme](./benchmark_readme); for the numbers, [published results](./results).
Figures quoted below are as they stood at the time of each entry and several were
superseded by later corpus changes — where an entry and the results page disagree, the
results page and the committed JSON are authoritative.

---

## Instrument defects found and fixed

### A capture-side false pass, and it flattered the target

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

### Two more false passes, and both flattered the target

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
  `--exhaustive-splits` makes it true.
- `limitations.method_limits` said "Three entity types" for as long as there have been
  four. It is derived from `AXES` now.

**Status at this historical audit point (subsequently fixed):** `capture.self_probe` was
fabricated, target and capture metadata were hardcoded, and claim citations came from
policy docstrings. The active reports now carry measured self-probe timing, the supplied
external target/model/capture values, and operator-supplied claim/configuration provenance;
the current-instrument and schema guards reject the obsolete report shape.

---

---

## Chronological re-measurement log

### Re-measured 2026-09-05 on a fixed instrument.

*(Superseded in part -- see the round 2 box below. This directory now holds thirteen rows
from the current instrument and twenty-four that predate it, and the build says so.)*

The leak inspector was fixed for the ninth and tenth time -- a `startswith("data: ")`
response parser, and ordered joins keyed by key name rather than by JSON path (see
§2.7) -- and `configured_upstream_boundary`, `sse_validity` and `capture.self_probe`
stopped being literals. **Every row in this directory was then re-run against live
containers and a real Google Cloud project.**

**Not one response-path RATE moved.** Five reference policies, both Presidio rows, all
four Google rows, and all four gateway rows reproduce their previous rates to the digit.
That is the evidence the fix closed blind spots rather than changing arithmetic, and it
is the only reason the older tables below can still be read next to the new ones.

**Correction, 2026-09-05, from the round 2 review of these repairs.** This paragraph
originally said "not one response-path NUMBER moved", and that was false as written.
Recomputed mechanically over every leaf of every artefact:
`checks.fragmentation_safety.events_observed_max` moved in **15 of 16 rows** (the
intended `worst`-to-`max` fix, but it is a published number and it is the `events`
column of the tables below); `portkey-gateway-oss`'s
`checks.response_fidelity.passed` flipped **false to true** and its denominator went
128 to 96, undocumented; and `nemo-guardrails`'s `checks.sse_validity.passed` went
**true to false**. What is true, and is the claim worth making, is that
`metrics.leak_rate.*`, `metrics.fidelity_rate` and `metrics.delta_frag` are byte-identical
in all 16 rows.

**What did change is the request path**, which no report could see while the check was a
hardcoded pass, and **the Higress row, which was withdrawn** because its plugin config was
not valid YAML and had never loaded. Both are covered below.

**Three rows were then added**: LLM Guard 0.3.16 in two integration modes and Guardrails
AI 0.10.2. Both are OSS libraries needing no account, and between them they state the
whole argument out of other people's software -- the one that retains cannot restore, and
the one that restores must choose between retaining and streaming.

`tests/conformance/test_results_are_comparable.py` now fails the build when a published
row's `inspection_scope` is not the one the current emitter generates, and when a sweep
file carries no `instrument` block. The existing corpus guards compare case definitions
and could not see an inspector change underneath them, which is exactly how a row scored
by a walk with six blind spots came to sit next to one scored without them.

### Round 2, 2026-09-05: the repairs above were reviewed, and seven more defects found

**Three of the seven were introduced BY a repair**, and one by the repair for the closest
neighbouring defect: `_fidelity_check` was narrowed to the measurable cases to fix a
denominator, and the narrowing made `response_fidelity.passed` vacuously **true** on
zero of them. Full list, demonstrations and directions:
`.llm/research/ieee-software/` review notes; regression tests in
`tests/conformance/test_v2_round_two_repairs.py`.

**13 of 37 artefacts have been re-run on the twice-fixed instrument and NOTHING MOVED**
-- not a rate, not an outcome, not a check verdict. The seven single-run rows and the
five `exhaustive-splits/` rows I could reach at Tier 0 and Tier 1 differ from what was
published only by *added* fields: `instrument`, `data_events_observed`, the regenerated
`inspection_scope`, and `leak_evidence` gaining a recovery tier. `passthrough` still
leaks 1.00/1.00, `retention-plus-decoding` is still the only `pass`, and
`presidio-chunk-local` under `--exhaustive-splits` is still 1.00 / DeltaFrag 0.875.

**`seed-sweep.json` -- the file this README calls "the numbers to cite" -- was re-run
in full: 7 policies x 12 seeds = 84 corpus runs, 2,688 cases. Mean, min, max and stdev
of all four metrics: 0 of 112 summary statistics moved.** That is the strongest control
available without a container or a billed project.

**Worth reading in the re-run rows**: `leak_evidence[].observed` used to be the constant
`normalized-match` for every leak. It now reports HOW the value was recovered, and in
every re-run row every leak is `literal`, `same-path-join` or `single-field`. **Not one
published leak rests on a cross-field concatenation** -- which is the tier a coincidence
can reach, and the tier the round 7 IPv4 false positive lived in.

**Round 3 (2026-09-06) -- two published fields in this directory were wrong, and neither
was a rate.** `one_character_events_requested` was `const: true`, then a derivation that
could only return `false`: it required BOTH halves of a split to be one character, which
needs a two-character value, and the shortest rendered corpus value is 11. Meanwhile
`--exhaustive-splits` cuts at offset 1 and the harness really does emit a one-character
event there. **The five `exhaustive-splits/` rows now report `true` and the seven midpoint
rows report `false`** -- the field discriminates between the two run modes for the first
time; before this it was `true` for everything, then `false` for everything.

`coalescing_not_distinguished` was `const: true`, defended as a limitation disclosure
rather than a capability claim. The category is real and the disclosure was false here:
v1 cannot tell "the gateway merged events" from "the upstream sent fewer" because v1 does
not control the upstream, but **v2 IS the upstream** and writes a known 3 or 4 data events
per case. All 12 fresh rows now report `false`, with `upstream_data_events_emitted` and
`coalescing_observed` beside it. This **strengthens the E15 reading**: `llm-guard-buffered`
and `litellm-presidio` received one data event against 3-4 sent, which is coalescing
*proved* rather than inferred from a low absolute event count. (Both are stale; the claim
is checkable once they are re-run.)

**Nothing else moved.** Every leaf of all 12 re-run rows, diffed against `31c1a21`:
**1,314 critical leaves compared -- rates, `passed` verdicts, outcomes, case counts -- and
0 moved.** The only non-noise differences are the two fields above (12 rows and 5 rows
respectively) plus the two added fields. Known-answer controls hold: `passthrough`
1.00/1.00, `retention-plus-decoding` the only `pass`, `presidio-chunk-local` still
0.50 -> 1.00 under `--exhaustive-splits`.

**A guard that was covering nothing.** `test_results_are_comparable.py` selected artefacts
with `RESULTS.glob("*.json")`, which is not recursive, so `exhaustive-splits/` -- nine
rows, **four with no `instrument` block at all** -- was checked by none of the six tests
that use it. Those four sat beside five fresh rows and the suite was green about them.
It is `rglob` now, and the stale count below rises from 24 to include them explicitly.

**Round 4 (2026-09-06): the billed calls had no deadline, no retry and no clock, and a
rate was published without its denominator.** `_gcp_post` was `urlopen(..., timeout=60)`
-- a literal, so the only two calls in this harness that cross the public internet were
the only two `--connect-timeout` / `--read-timeout` could not reach, and `urllib` takes
ONE number where connect and read need two. Nothing retried, against APIs quota'd per
project per minute that this profile calls once per delta. And the access-token cache had
no clock at all, against a token Google issues for one hour, so any run longer than an
hour got a 401 for every remaining case. None of the three is a measurement error; each
ends a BILLED run partway through and writes nothing. Separately,
`outcome_rationale` -- the field that states the result -- carried four rates and no
denominator while `method_limits` three fields away said "32 cases". **The denominator is
`cases_applicable`, never `cases_scored`** (which counts the cases ATTEMPTED), and the
three rates have three different denominators: fidelity over the echo-observable cases,
each leak rate over its own fragmentation arm. All four now say so.

**16 artefacts re-measured on the resulting instrument (`inspector_sha256`
`955edd079f406e13`) and every headline rate and outcome reproduced.** Seven single-run rows, five
`exhaustive-splits/` rows and all four Google rows, at the published seed
`a1b2c3d4e5f60001`: LeakRate, FidelityRate, DeltaFrag, outcomes, corpus digests and
case digests were identical. That narrower claim matters: a later leaf-by-leaf audit
found 237 changed common leaves, including 80 naturally variable timing measurements
(`capture.self_probe.round_trip_ms` and `client_observed_latency`). Other expected
movements included `outcome_rationale` in all 16 rows (the denominator now appears in
it) plus, in the four
Google rows only, `coalescing_not_distinguished` and `one_character_events_requested`
going `true` -> `false`. Both of those are the round 3 repairs landing on rows that
predated them, and both are now correct: the Google rows are midpoint runs, and no half
of an 11-character value is one character. Known-answer controls hold: `passthrough`
1.00/1.00, `retention-plus-decoding` the only `pass`, `presidio-chunk-local` 0.50 ->
1.00 under `--exhaustive-splits`, Google DLP 0.5/1.0/0.5 and 0.5/0.5/0.0, Model Armor
0.75/1.0/0.25 and 0.75/0.75/0.0.

**Read against the previous note: the four Google rows now carry a coalescing RATE.**
They were the rows with no `instrument` block at all, so they had never been scored by
the round 3 instrument; they are now on the same footing as every other row here.
All 16 share one `instrument` block and one `fragmentation_safety` shape.

**8 artefacts are STALE and `test_every_artefact_records_the_instrument_that_produced_it`
is RED because of them**, correctly. They are the eight external gateway rows -- LiteLLM,
Portkey, NeMo, both Shield 1.6.0 configs, LLM Guard x 2 and Guardrails -- and they are the
last artefacts here carrying no `instrument` block at all. The commands to re-measure them
are staged in `benchmarks/rerun-external-gateway-rows.sh`, each with the bearer token,
header block and `V2_REQUEST_PATH_REDACTION` the published row was measured under. **Do
not read them beside the fresh rows, and do not weaken the guard to make the build
green.** Re-run them or move them out.

**All nine `seed-sweep*.json` files were regenerated on the current instrument and every
summary statistic reproduced.** `seed-sweep.json` -- 7 policies x 12 seeds, 84 corpus runs,
2,688 cases, the file this README calls "the numbers to cite" -- came back with **700
statistics identical and 0 moved**. Across all nine, 1,138 statistics compared and the only
movement was transport, not measurement:

* **Portkey seed 3 got CLEANER**: `inconclusive` 2 -> 0, `echo_observable` 23 -> 24, all
  four rates unchanged. The published row had two cases die and reported `cases: 32` beside
  rates computed over 30 -- which is precisely the denominator defect fixed this round. The
  regenerated row carries `cases_applicable` and `cases_attempted` separately, so the next
  occurrence is visible instead of silent.
* **Both LLM Guard sweeps lost 1-2 cases on the first attempt** and their rates moved with
  the denominator; `llm-guard-buffered`'s `delta_frag` went 0.0 to **-0.0458** purely from
  losing one adversarial case, which is the unpaired-population artefact this README warns
  about, arriving by transport rather than by design. Re-run ONCE (not until clean): both
  came back 0 inconclusive on all six seeds and reproduced every published sweep summary
  statistic exactly. Per-case wall-clock timings are not part of the sweep summaries and
  naturally vary. **The flakiness is recorded rather than hidden** -- LLM Guard loads transformer
  models per request and its own STATUS.md already documents a run dying partway through
  the fourth seed. Treat a `delta_frag` that is slightly negative on this target as a
  dropped case until proven otherwise.

### Round 6, 2026-09-08: the Presidio exhaustive result is now a 12-seed result

The 24 reports under `exhaustive-presidio-seed-sweep/0000000000000001/` through
`000000000000000c/` enumerate every internal two-part split for both Presidio wrappers
over the same generated values as the midpoint sweep. Two more reports re-run the
published seed. All 26 carry inspector digest **`3ac1f621aa008d04`**, FidelityRate 1.00,
`fragmentation_strategy: exhaustive-2-part`, and zero inconclusive cases.

Across the twelve sweep seeds, chunk-local LeakRate(adversarial) is **1.00 on every
seed** and DeltaFrag is mean **0.8333**, range **0.625-0.875**. The matching midpoint
ranges are 0.25-1.00 and -0.125-0.875. Enumeration therefore removes the adversarial
LeakRate's seed variation rather than merely selecting a favourable seed. Retention
keeps DeltaFrag at **0.00 on every seed**. Tag `v2-evidence-round-6` preserves the
reports; the directory README gives the complete summary and reproduction command.

### Round 7, 2026-09-09: worst-case unions, FIDE, and an external exhaustive control

The current instrument (`inspector_sha256` **`94262e29a492ab6a`**) now distinguishes
adversarial partitions from uncut baseline requests and publishes the exact oracle in
every report. The v2 tree contains **95 schema reports** plus nine sweep aggregates:
the previous 54 reports, 29 new worst-case/12-seed Presidio reports, and 12 exhaustive
LLM Guard reports. All refreshed v2 headline leaves reproduced; none moved.

At the published seed, the union oracle drove **934 two-part** and **20,959 three-part**
adversarial partitions across 32 cases, plus 32 uncut baselines. Adding the third piece
changed no verdict: chunk-local detection already saturated under the two-part oracle,
while retention stayed at its single-chunk baseline. The separate 64-case FIDE profile
extends the same instrument to PII and secrets; see `../fide-v2.1/README.md`.

LLM Guard 0.3.16 supplies an independent detector control. Across six seeds and every
internal two-part split, the chunk-local wrapper leaked adversarially at **1.0000 on all
seeds** (mean DeltaFrag **0.8333**, range 0.7500-1.0000). Whole-response buffering held
DeltaFrag at **0.0000**, at the cost of incremental delivery. One partial buffered row
and one listener failure were preserved outside the published tree before bounded
recovery; every published row is complete.

The first bounded external-sweep attempt lost one Shield case and four Portkey cases.
Their first attempts were preserved, and the single prescribed rerun completed 32/32
for every seed while reproducing every summary statistic. This is transport history,
not a silently retried measurement.

### Round 5, 2026-09-07: coalescing evidence now follows the response attempt

Five instrument defects were repaired together. The capture records each upstream
response incrementally, including partially written responses; `run_case` pairs the
client result with the records created by that same gateway attempt; zero or multiple
upstream responses are retained as empirical observations but excluded from the
coalescing comparison; transport-error cases remain in the per-case table as stream
failures; and first-split event accounting uses the first split's own `[DONE]` fact.
The per-case evidence now includes `upstream_responses_observed`. The capture producer
and frame generators are also part of `_INSTRUMENTED`, moving `inspector_sha256` to
**`3ac1f621aa008d04`**.

**All 28 single-run artefacts and all nine sweep files were regenerated. Every headline
rate, outcome, corpus digest, case digest, and sweep summary statistic reproduced.** The
sweeps cover 132 corpus runs (84 local and 48 external). Individual per-case timings
varied naturally and are not part of that reproducibility claim.
Three external sweep attempts were transiently inconclusive on the retained re-run:
one case in each of LiteLLM seeds 4 and 5, and one in Portkey seed 6. The prior sweeps
had zero for those seeds. Their rate rows and every sweep summary statistic still
reproduced; the case-count movement is recorded here rather than hidden by re-running
until clean.

The intended non-headline correction appears in NeMo. Its eight documented HTTP 422
cases still leave 24 applicable cases and a 24-case coalescing denominator, but now
report `stream_failure_cases: 8` instead of 0. No other framing aggregate moved. The
external rerun script also now exports the repository package path and exits on the
first failed emitter invocation; previously eight import failures could still lead to a
misleading success footer.

- Emitter: `pii-leak-benchmark/pii_leak_benchmark/v2_emitter.py` (committed)
- Reproduce: `python -m pii_leak_benchmark.v2_emitter --validate --out <scratch-dir>`
  **`--validate` WRITES.** It is not a dry run: it emits each policy's report to the
  output directory like a normal run. `--out` is now required because the earlier default
  was *this published directory* and a verification command was measured overwriting its
  artefacts in place. Run it from the repo root; the schema path is CWD-relative.
- Schema: `spec/v2.0.0/http-profile.schema.json`
- Reports on the current instrument (95): all 19 single-seed JSON reports in this
  directory, all nine reports under `exhaustive-splits/`, 26 archived Presidio reports
  under `exhaustive-presidio-seed-sweep/`, 29 worst-case/12-seed Presidio reports, and
  12 exhaustive LLM Guard reports. All nine `seed-sweep*.json` files carry the same
  instrument block.
- The two `presidio-*` policies require a live analyzer on `127.0.0.1:5002`. Select a
  subset with `--only`, e.g. `--only chunk-local,bounded-retention`.

**What this establishes:** `spec/v2.0.0`'s echo/injection response split is now a
**demonstrated** design, not only a specified one. Before this run the v2 directory held a
schema and a README with no emitter and no report; C3 in `manuscript-v2.md` was a design
claim a reviewer could reasonably discount.

---

**All 95 single-run artefacts in this tree come from one corpus definition: 32 cases, 5
axes, 76/76 pairs.** `tests/conformance/test_results_are_comparable.py` walks the tree
recursively and fails the build if that stops being true, because on 2026-09-04 this
directory briefly held artefacts from four different corpus generations at once --
individually correct, jointly misleading. A stale row is worse than a missing one: a
missing row is visibly absent, a stale one looks like evidence.

The top-level `seed-sweep*.json` files are the midpoint numbers to cite. The exhaustive
Presidio distribution is recomputed from the twelve numbered directories under
`exhaustive-presidio-seed-sweep/`; the extra published-seed directory is a reproduction,
not a thirteenth sweep seed.

## Related

- [Benchmark readme](./benchmark_readme)
- [Published results](./results)
- [Reproduce the fragmentation result](./reproduce-fragmentation)
