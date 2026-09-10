# FIDE profile results (draft spec v2.1.0)

Fragmentation-Induced Detection Evasion is a property of the **transport**, not of personal
data. A boundary placed inside a value that the client reassembles is invisible to a
per-event detector whatever the value means. The v2 corpus in `../v2-response-split/`
measures that for PII only. This corpus adds a `needle_class` axis and measures PII and
secrets **in the same run, against the same detector configuration**, so the comparison
between classes is a contrast within one row rather than two directories read side by side.

## Do not compare these rows with v2 rows cell for cell

They are a different corpus generation. This corpus has six axes and 64 cases; v2 has five
axes and 32. Their `corpus.sha256` values differ and they are deliberately in separate
directories. `tests/conformance/test_published_profiles.py` checks each directory on its
own terms and asserts nothing across them — except one thing.

**The one thing asserted across both**: the two profiles carry the **same**
`instrument.inspector_sha256`. The FIDE emitter imports every function that decides a
number from `v2_emitter` rather than forking it. That identity is the entire basis on which
a PII result and a secret result may be discussed in one sentence, and a test fails if the
two directories ever drift onto different inspectors.

## The corpus

Six axes. Five are v2's, unchanged; `needle_class` is the new one.

| axis | values |
|---|---|
| `entity` | `EMAIL`, `SSN`, `CARDPAN`, `USPHONE`, `AWSKEYID`, `GHTOKEN`, `SLACKBOT`, `PEMBLOCK` |
| `needle_class` | `pii`, `secret` |
| `encoding` | `plain`, `percent` |
| `fragmentation` | `single_chunk`, `adversarial` |
| `carrier` | `sse-delta-content`, `sse-json-field` |
| `request_site` | `chat-content`, `system-content`, `unrecognised-key`, `tool-description` |

`entity` and `needle_class` are **constrained**: a needle belongs to exactly one class, so
`(AWSKEYID, pii)` is impossible rather than uncovered. `pairs_required` counts only pairs
some case can carry, or `proof_complete` could never be true and the schema's coverage gate
would be unsatisfiable.

Every case has a fragmentation twin that matches on all five other axes, so DeltaFrag is a
difference of marginal rates over matched pairs. The paired 2×2 table behind it is
published in `metrics.discordance`.

### A third class is declared and deliberately empty

`prompt_injection` is in the registry with no fixtures behind it, and every report lists it
in `corpus.needle_classes_declared_unmeasured`. That is a scope statement, not an omission.
Populating it needs a separate review of fixture safety, claim scope and disclosure, and it
must not arrive as a side effect of a change that lands the secret family.

## The needles

The four PII needles are drawn per seed by the same generator the v2 corpus uses, so the
two corpora cannot drift on the entity both measure. The four secret needles are **fixed
literals**, and `corpus.needle_registry_sha256` pins them — `corpus.seed` says nothing
about a value that does not vary.

Full provenance for every fixture, including the retrieved primary source, the
non-liveness argument and the documented detector claim, is in
`pii_leak_benchmark/needle_registry.py` and in the ledger under
`.llm/research/ieee-software/fide/`.

**Seed variation is zero for half this corpus, by construction.** The secret rates and
their DeltaFrag are single-valued point results, so no seed-level interval is identified
for them and none is reported. `fixture.value_space_nominal` publishes `1` for each fixed
needle, which is the schema's own encoding for "this entity does not vary" — so the claim
is checkable from the artefact rather than only from prose.

## The targets

The reference controls are **study-owned models, not products**. Their detector set is the
v2 PII patterns plus four secret patterns transcribed from detect-secrets 1.5.0 denylists,
cited per pattern. Nothing here is a result about detect-secrets: it is not a streaming
scanner and does not claim to be one.

| control | what it models |
|---|---|
| `fide-passthrough` | forwards everything; leaks both arms |
| `fide-redact-all` | one-way anonymiser; destroys the echo |
| `fide-chunk-local` | the modelled defect: correct per event, blind across events |
| `fide-whitespace-retention` | v2's retention rule, ported verbatim including its whitespace assumption |
| `fide-length-bounded-retention` | retention bounded by the longest protected unit, with no whitespace assumption |
| `fide-retention-plus-decoding` | the above plus decoding before detection |

**Why the retention control is two controls.** v2's `bounded-retention` cuts its buffer at
the last space before the tail. That is sound if and only if every protected unit is a
whitespace-free run, which is true of every v2 needle and **false of a PEM block**. Both
rules are measured here, so "retention removes the fragmentation increment" gets its
boundary condition measured rather than assumed.

One shipping product is eligible for the secret family: **LLM-Shield-Proxy 1.6.0**, whose
published documentation names `AWS_API_KEY`, `GITHUB_PAT` and `SSH_PRIVATE_KEY`. It
documents no Slack pattern, so `SLACKBOT` is `not-applicable` for it. The author maintains
that gateway; see the claim-scope audit for how that conflict is handled. Every other
measured integration is a PII scanner and is `not-applicable` for this family — which is
recorded as a claim fact, not measured as a miss.

## Reference-control result

The midpoint study contains one published-seed run of all six controls and twelve
systematic seeds for the three controls whose result can vary with generated PII.
The two-part study uses every internal split over those same twelve systematic seeds.
The union study adds every internal three-part partition at the published seed. Secret
fixtures are fixed literals, so twelve identical secret-class rows are not treated as
twelve independent observations.

| control and oracle | Fidelity | PII single / adversarial / DeltaFrag | secret single / adversarial / DeltaFrag |
|---|---:|---:|---:|
| `fide-chunk-local`, midpoint | 1.0000 | 0.0625 / 1.0000 / 0.9375 | 0.0625 / 0.5625 / 0.5000 |
| `fide-chunk-local`, every two-part split | 1.0000 | 0.0625 / 1.0000 / 0.9375 | 0.0625 / 1.0000 / 0.9375 |
| `fide-chunk-local`, two-part plus three-part union | 1.0000 | 0.0625 / 1.0000 / 0.9375 | 0.0625 / 1.0000 / 0.9375 |
| `fide-whitespace-retention`, union | 1.0000 | 0.0625 / 0.0625 / 0.0000 | 0.2500 / 0.2500 / 0.0000 |
| `fide-length-bounded-retention`, union | 1.0000 | 0.0625 / 0.0625 / 0.0000 | 0.0625 / 0.0625 / 0.0000 |
| `fide-retention-plus-decoding`, union | 0.9688 | 0.0000 / 0.0000 / 0.0000 | 0.0625 / 0.0625 / 0.0000 |

For the union run, 32 adversarial cases produce 934 two-part splits and 20,959
three-part partitions. The reports therefore record 21,893 adversarial partitions plus
32 uncut baseline requests, or 21,925 captured requests per policy. No case reaches the
6,000-per-family cap. Adding the third piece changes no case verdict: the two-part oracle
already saturates the chunk-local result on this corpus, while retention stays at its
single-chunk baseline.

The whitespace-retention row is the important baseline warning. Its DeltaFrag is zero,
but it leaks every multi-line PEM case in both arms because its own whitespace cut divides
the protected unit. Bounding retention by the longest protected unit removes that leak.
Zero therefore does not mean containment; it means only that fragmentation did not change
the rate.

## Shipping-product result and bounded exhaustive gap

LLM-Shield-Proxy 1.6.0 response-on completed the 64-case midpoint profile with
FidelityRate **1.0000** and no inconclusive cases. The raw corpus-wide rates were
0.1875 single-chunk, 0.4062 adversarial, and DeltaFrag 0.2187. Because the product does
not document a Slack token detector, the claim-scoped view excludes `SLACKBOT`: over its
seven enabled entities the corresponding rates are 0.0714, 0.3214, and 0.2500. Within
the documented secret scope alone they are 0.0833, 0.5000, and 0.4167.

No exhaustive shipping-product row is published. After correcting the required
`SHIELD_ENCRYPTION_KEY` configuration, the first bounded attempt completed 62/64 logical
cases and the single prescribed retry completed 63/64; both failed schema validity because
an upstream disconnect makes the paired statistic incomplete. The attempts are retained
in the private evidence ledger. The midpoint point result is valid, but no exhaustive
claim about the shipping product follows from it. This distinction is especially important
because the product is maintained by the study author and is the only eligible shipping
secret-scanner row.

## Raw reports and the derived aggregate

`fide-sweep.json` is a derived, audited aggregate rather than a profile report. Its
`summary_seeds` field excludes the hand-picked publication seed whenever systematic seeds
exist, and its `seed_variation_note` states that the fixed secret literals do not identify
a seed-level distribution.

Product reports retain the full 64-case corpus so every row shares the same corpus digest.
A product may document a narrower detector set. For that case the aggregate also publishes
`claim_scoped` rates recomputed only over `entity_scope.enabled`; unsupported entities are
excluded as not applicable, not counted as misses. Both the corpus-wide raw view and the
claim-scoped product view remain available and are checked against the named reports by
`benchmarks/fide_numeric_audit.py`.

## Reproduce

Run from the repository root. Every command writes to staging first:

```bash
PYTHONPATH=pii-leak-benchmark python benchmarks/fide_sweep.py midpoint twopart worstcase
PYTHONPATH=pii-leak-benchmark python benchmarks/fide_sweep.py shield summarise
python benchmarks/fide_numeric_audit.py
```

The Shield stage needs the documented version 1.6.0 response-on listener and capture route;
`benchmarks/fide_sweep.py` supplies its measured token and request-path configuration.
The container must also receive an operator-supplied 32-byte `SHIELD_ENCRYPTION_KEY`, which
version 1.6.0 requires for its end-of-stream digest receipt. The round-7 container used a
benchmark-only non-production key; the key material itself is not part of the report.
