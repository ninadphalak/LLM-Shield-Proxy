---
sidebar_position: 2
title: Benchmark readme
---

# Benchmark readme

What the v2 response-split profile measures, and the findings a reviewer should check.

The numbers themselves are on [published results](./results), generated from the committed
JSON. This page explains what they mean. To re-derive two of them yourself in about two
minutes, see [reproduce the fragmentation result](./reproduce-fragmentation).

## What is being tested

Each row is a *response-path policy*: a rule for what a gateway does to the model's reply
on its way back to the user. The profile sends one response containing two segments that
need **opposite** treatment.

- The **echo** segment is the user's own data coming back. The gateway masked it on the way
  out, so it must put the real values **back in**. Failing here means the user gets
  `[EMAIL_1]` instead of their own email address - a broken product.
- The **injection** segment is data the user never sent, arriving from upstream. The gateway
  must **take it out**. Failing here means someone else's data reaches the user - a leak.

A gateway is not being asked to apply one rule to the response. It is being asked to apply
two opposite rules to two segments of the same response. A profile measuring only one
direction cannot tell a correct gateway from a destructive one.

## The four numbers

| Column | Question it answers | Good value |
| :--- | :--- | :--- |
| **Fidelity** | Did the user get their own data back? | `1.00` - all of it |
| **Leak, single chunk** | When values arrive whole, does anything leak? | `0.00` - nothing |
| **Leak, fragmented** | When values are split across chunks, does anything leak? | `0.00` - nothing |
| **DeltaFrag** | How much worse does splitting make it? | `0.00` - splitting changed nothing |

DeltaFrag is the third column minus the second. It is the headline because it isolates the
streaming defect: **a policy can score perfectly on the second column and still fail the
third**, and only the gap between them shows it.

To pass, a row needs Fidelity `1.00` and both leak rates `0.00`.

### DeltaFrag `0.00` is not good news on its own

`passthrough` forwards bytes untouched. It scores Fidelity `0.00` (never rehydrates, so the
user sees placeholders), leak `1.00` in both arms (never redacts), and DeltaFrag `0.00` -
because it is equally broken whether or not values are split.

Always read DeltaFrag next to the single-chunk baseline. Zero means fragmentation changed
nothing, which is equally true of perfect protection and of total failure.

### A negative DeltaFrag is possible and does not mean safety

`presidio-chunk-local` reaches `-0.125` on one seed of twelve: the value leaked *more* when
not fragmented. The detector missed a complete US phone number but matched one fragment of
it for an unrelated reason. A negative difference must be read beside the single-chunk
baseline and the report's `detector_blind_entities` field.

## Findings a reviewer should check

### 1. No single global response-path policy passes

`passthrough` and `redact-all` fail in **opposite directions**. Passthrough returns
everything, so the echo survives but every injected value reaches the client. Redact-all
suppresses injected values in the single-chunk arm but destroys the echo, scoring Fidelity
`0.00`. Neither is a partial pass.

### 2. DeltaFrag separates policies that are otherwise indistinguishable

`chunk-local`, `bounded-retention` and `retention-plus-decoding` are **identical under the
single-chunk condition**. A benchmark that only sent whole values inside single chunks
would rank all three equal.

Under fragmentation they separate: `1.00`, `0.125`, `0.00`. DeltaFrag is exactly that gap -
how much of a policy's apparent correctness is an artefact of being tested on unfragmented
input.

### 3. The result holds for a real detector, not just the models

`presidio-chunk-local` and `presidio-retention` drive a live
`mcr.microsoft.com/presidio-analyzer` container with the stock recognizer registry, queried
per delta. Same container, same fixture, same corpus. The only difference between the two
rows is whether a chunk boundary may fall inside a value.

Mean DeltaFrag over 12 seeds falls from `0.5521` to `0.00` on that change alone. Both leak
identically in the single-chunk arm, so a benchmark that never fragmented would rank them
the same.

Two things this rules out:

- **Not an artefact of a toy detector.** The reference `chunk-local` policy and the live
  Presidio one fail by the same mechanism. The real detector is somewhat better and varies
  by seed, which is what a real detector should do.
- **Not a Presidio defect.** Presidio makes no streaming claim; applying it per chunk is
  the integrator's decision, and the property belongs to the integration pattern. The
  rehydration half is the wrapper's, so Fidelity here does not describe Presidio at all.

### 4. Retention fixes fragmentation and does not fix encoding

`bounded-retention` holds back a bounded tail, so no value straddles a chunk boundary
undetected. It still leaks - and the per-axis breakdown says exactly where:

| Axis value | Leaked / applicable |
| :--- | ---: |
| `encoding=plain` | 0 / 22 |
| `encoding=percent` | 4 / 10 |

A percent-encoded address contains `%40`, not `@`, so the detector never fires however much
buffer is held. `retention-plus-decoding` adds decoding before detection and closes it.

**Encoding and fragmentation are independent defects requiring independent mitigations.**
That is the argument for keeping them as separate corpus axes. The live detector has the
same blind spot: `presidio-retention` leaks the same percent-encoded case on every seed.

### 5. The corpus block cannot be satisfied by a partial run

`corpus.coverage.axes` requires all five axes - `entity`, `encoding`, `fragmentation`,
`carrier`, `request_site`. A single-axis sweep cannot produce a valid v2 report.

The generated array is **32 cases covering 76 of 76 pairs**, with `proof_complete: true`
recomputed from the emitted cases rather than asserted. It is also **twinned**: 16 cases in
each fragmentation condition with identical populations on the other four axes, so DeltaFrag
is a within-case difference. A greedy pairwise array alone gave 8 against 4, which made
DeltaFrag attribute a composition difference to fragmentation.

### 6. The instrument has been wrong, repeatedly, and always in the same direction

Ten inspector defects have been found and fixed in this harness. Every one of them
**flattered the target** - a leak instrument that fails toward "secure" is the one failure
mode it must never have.

The clearest example: a capture server that reused a socket across cases let Portkey, a
gateway that redacts nothing, score leak `0.33` instead of the true `1.00`.

This history is not a footnote, and it is the main reason to distrust a clean number you
did not produce yourself. It is recorded in full on the
[benchmark revision history](./benchmark-revision-history).

## Verify it without trusting the numbers

```bash
# Re-run a policy. Rates reproduce; drawn values differ unless you pass --seed.
python -m pii_leak_benchmark.v2_emitter --out ./benchmark-output/v2 --only chunk-local

# Recompute the covering-array proof independently.
python -c "
from pii_leak_benchmark.v2_emitter import covering_array, _all_pairs, _pairs_of
ca = covering_array(); cov = set()
for c in ca: cov |= _pairs_of(c)
print(len(ca), 'cases |', len(_all_pairs() & cov), 'of', len(_all_pairs()), 'pairs')
"

# Validate a published report against the published schema yourself.
python -c "
import json, jsonschema
schema = json.load(open('spec/v2.0.0/http-profile.schema.json'))
report = json.load(open('benchmarks/results/v2-response-split/chunk-local.json'))
jsonschema.Draft202012Validator(schema).validate(report)
print('valid')
"
```

Run these from the repository root. The emitter resolves `spec/v2.0.0/` against the working
directory.

## Limits

- **Reference policies are models, not products.** They occupy deliberate corners of the
  space. Only the external-gateway rows measure a vendor's shipped streaming path.
- **The `presidio-*` rows are still models.** They drive a real, widely deployed *detector*
  inside a wrapper written here; the rehydration half is the wrapper's.
- **Loopback transport**, single machine. Latency figures are in-process and must not be
  cited as gateway overhead on a network.
- **32 cases, pairwise not exhaustive** - four entity types, two encodings, two carriers,
  four request sites, two fragmentation conditions.
- **Fragmentation is a single midpoint split** in the headline rows, not every split point.
  Exhaustive and union oracles are separate, longer runs under `exhaustive-splits/` and
  `worst-case-splits/`.
- **The capture is not a model.** The upstream is a synthetic SSE emitter, so the injection
  segment is what the harness chose to inject. This is a limit on *generalization*, not on
  measurement. Every needle is known exactly, so the gateway's handling of it is measured
  rather than estimated: `LeakRate` answers "when this value arrives, split this way, does
  the gateway remove it before the client sees it?" and the answer is exact. What a
  synthetic upstream cannot supply is how often a real model emits such a value unprompted.
  That frequency is the missing quantity, which is why leak rates are conditional on an
  injection pattern rather than on observed model output.
- **It is not a leaderboard.** One version and one configuration each, run by the author of
  one of the measured products. A leaderboard needs replication, version ranges, more
  carriers, and someone else running it.

## Related

- [Published results](./results) - the generated tables.
- [Reproduce the fragmentation result](./reproduce-fragmentation)
- [Benchmark revision history](./benchmark-revision-history) - every instrument defect found and fixed.
- [Submit a run](./submitting)
