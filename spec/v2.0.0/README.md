# Streaming Privacy Gateway Conformance Specification v2.0.0

**Status: draft.** The schema is published and tested; no harness emits it yet. The
Axis C fragmentation harness is written against this document, not the other way round
— that ordering is the point of publishing the schema first.

Normative changes and result labeling follow the
[public governance process](../../website/docs/conformance/governance.md).

`spec/v1.0.0/` is **frozen**. Published artifacts pin it and cannot be re-executed, so
v2 is a new directory and a new `$id`, and both are supported. A v1 report is not a v2
report and the schema rejects it rather than accepting it with every corpus block
missing.

| File | Covers | Emitted by |
|---|---|---|
| `http-profile.schema.json` | OpenAI-compatible HTTP gateway profile, corpus run | `pii-leak-benchmark` (not yet) |

---

## 1. Why v2 exists: the response split

v1 measures leakage in **one direction** — values the client sent, recovered at the
configured-upstream capture. Transport fragmentation cannot be measured there:

- The harness does not choose where a gateway chunks its own upstream request.
- A gateway that buffers and JSON-parses a request body — nearly all of them — has no
  chunk boundaries to place at all.

Everything the harness *can* fragment is on the **response** path, because the capture
server is the gateway's configured upstream and is the harness's own code. Chunk
boundaries there are placeable at will, against a gateway nobody controls. So v2
measures leakage where the control actually is.

The response therefore carries two segments, **in separate bytes**:

| Segment | Contents | A correct gateway | Measures |
|---|---|---|---|
| `echo` | replays the prompt the gateway sent upstream | **restores** the original values | `FidelityRate` |
| `injection` | fixture values that were **never in the prompt**, emitted as a model would emit them, fragmented per the Axis C strategy under test | **redacts** them | `LeakRate`, and `DeltaFrag` from its two arms |

The property that makes this hard to satisfy by policy: **a correct gateway must behave
differently on the two segments.** Pass everything through and injection containment
fails. Redact everything and echo fidelity fails. No single global rule passes both —
which is exactly what v1's three-format fixture could not force, and why its README
conceded that a program written for those three formats passes without being a
detector.

### Separation is a check, not an assumption

`segment_separation` proves the two segments cannot be confused. If a value appeared in
both, a gateway that correctly rehydrated the echo would be indistinguishable from one
that leaked the injection, and the profile would publish a failure against a gateway
that did the right thing — the same class of error as the invalid-fixture incident,
where the harness penalised correct behaviour.

Three assertions, all required for the check to pass:

- `values_disjoint` — no value in both segments.
- `normalized_forms_disjoint` — asserted on the **normalized** forms, because the leak
  matcher strips separators and folds look-alikes, so two values differing only in
  punctuation are one needle to it.
- `injection_absent_from_request` — the injected values were never sent upstream,
  verified against the capture record. This is what makes an injected value at the
  client a *leak* rather than a restore: there is no masked original for the gateway to
  have rehydrated.

`shared_substring_max` publishes the margin, the way `needle_proximity` does, so a
reader can see how close the segments came to colliding rather than taking disjointness
on trust.

---

## 2. The metric

```
LeakRate(condition) = leaked cases / applicable cases
FidelityRate        = cases where the client got the original value back
DeltaFrag           = LeakRate(adversarial) - LeakRate(single-chunk)
```

`DeltaFrag` is the headline. It isolates how much of a detector's apparent correctness
is an artifact of being tested on whole strings: a gateway with a perfect static score
and `DeltaFrag = 0.34` has a streaming bug its own test suite cannot see.

`FidelityRate` is reported alongside, **always**. A gateway that redacts everything and
restores nothing scores a perfect `LeakRate` and is useless; v1's
`no-leak-profile-not-met` outcome already encoded that and the metric must not lose it.

`cases_by_condition` requires **both arms to be non-empty**. A delta computed without a
control condition is not a delta, and the schema's minimums are what stop one being
published.

---

## 3. What the schema enforces, and what it only records

Enforced structurally — a violating document fails validation:

1. Any check `passed: false` forces top-level `passed: false`. *(v1 rule, extended to
   the two new checks.)*
2. `metrics.leak_rate.overall > 0` forbids `passed: true`. A leaked case cannot round
   away into a pass.
3. `metrics.fidelity_rate < 1` forbids `passed: true` — a non-pass, but never a `fail`.
4. `metrics.cases_inconclusive > 0` forbids `passed: true`. v1's fail-closed rule,
   aggregated: an uninspectable capture is never an assumed no-leak.
5. `corpus.coverage.proof_complete: false` forbids `passed: true`.
6. `injection_check` cannot pass with a non-empty leak list, an unconfirmed delivery,
   or an uninspectable client capture — and cannot pass with an **empty**
   `injected_entity_types`, because injecting nothing and leaking nothing is not a pass,
   it is a run that did not happen.
7. `segment_separation` cannot pass without all three separation assertions.
8. `outcome: "fail"` requires measured leak evidence, now from **either** path.
9. `outcome: "no-leak-profile-not-met"` requires a clean injection segment too, so a
   response-path leak cannot be relabelled as the no-leak outcome.
10. Entity ids are capped at **10 ASCII characters** — see §5.

Recorded but **not** enforced, because JSON Schema cannot do arithmetic:

- `metrics.derivation_recomputed` — the harness recomputed `delta_frag` from the two
  leak rates it published and refused to emit on a mismatch.
- `metrics.sidecar_case_count_matches` — `cases_scored` equals the sidecar's record
  count.
- `entity_scope.partitions_corpus` — the three scope lists cover the registry exactly
  once.
- `corpus.sha256` being present in the published manifest registry.

These are `const: true` so they cannot be omitted or negated, which is the same trust
model as v1's `payload_content_included: false`. **They are not a defence against a
hand-forged report**, and nothing in either schema is. Stating that plainly is cheaper
than being caught implying otherwise.

---

## 4. Per-case results go to a sidecar

500 cases inline makes a report unreadable and therefore unreviewable, and an
unreviewable artifact is not a citable one. The report carries aggregates plus
`cases_digest`; the sidecar carries the cases.

---

## 5. Migration from v1

- `schema` becomes `llm-shield.streaming-privacy-http-profile/v2.0.0`.
- Four new required top-level blocks: `corpus`, `metrics`, `cases_digest`,
  `entity_scope`.
- Two new required checks: `response_injection_containment`, `segment_separation`.
- `response_fidelity` gains `segment: "echo"`.
- `configured_upstream_boundary` keeps its meaning — request-path egress — and only its
  `inspection_scope` string changes, because the inspector now folds NFKD, non-Latin
  digits and UTS #39 look-alikes rather than deleting them.
- **Entity ids are renamed to the corpus registry's.** v1's `CREDIT_CARD` is 11
  characters and does not fit the budget; the registry id is `CARDPAN`. The budget is a
  streaming constraint, not a style rule: the vault's look-behind retention is
  `L = N - 1` where `N` is the maximum placeholder length, and the placeholder derives
  from the entity id, so every character on the *longest* id widens the window the SSE
  rehydration buffer holds on the hot path for every request — whether or not that
  entity is ever seen.

---

## 6. `entity_scope` is required, and that is deliberate

Gateways select which entity classes they recognise — by region profile, by recognizer
registry, by their own configuration. **A gateway that never had an entity class
enabled has not passed that class; it was not tested.** v1 already carries
`redaction-not-enabled` for "offers redaction, was not turned on"; this is the same
condition at entity-class granularity, and without it a published row can claim a pass
it never earned.

`unknown` is an honest answer and is not a pass: cases for those entities are scored
inconclusive, never clean — and by rule 4 above, inconclusive cases forbid a pass.

`source` and `mechanism` are operator-supplied and unverifiable by the harness, which
is why they are labelled rather than trusted.

---

Implementations may reuse the specification and schema under the repository's Apache
License 2.0. Conformance claims must state the exact version and must not imply
certification by the LLM-Shield-Proxy project.
