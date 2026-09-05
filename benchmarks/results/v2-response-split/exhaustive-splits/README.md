# The same nine policies, scored by the exhaustive split oracle

Produced with `--exhaustive-splits`, which cuts each adversarial value at **every internal
offset** instead of once at its midpoint, and fails the case if **any** split leaks. Same
corpus, same seed (`a1b2c3d4e5f60001`), same inspector as the rows one directory up. The
only variable is where the value is cut: 252 splits over 16 adversarial cases.

These are in a subdirectory on purpose. The guards in
`tests/conformance/test_results_are_comparable.py` glob the parent directory
non-recursively, so these rows are not compared case-for-case with the midpoint rows, which
is right: they are the same policies under a different oracle, not different policies.

Reproduce any of them:

```bash
python -m pii_leak_benchmark.v2_emitter --only presidio-chunk-local --exhaustive-splits \
  --seed a1b2c3d4e5f60001 --out /tmp/exhaustive
```

## What they show

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

**One row moves.** `presidio-chunk-local` nearly doubles: every adversarial case leaks at
some split point, and the midpoint happened to land on cuts Presidio still caught in half of
them. The midpoint was under-reporting a live detector's fragmentation failure by half.

**Nothing else moves, and that is the more useful half.** The modelled policies are
deterministic regexes, so where the cut falls cannot change the answer. And every retention
row holds DeltaFrag at exactly 0.00 across every internal split point of every value.
Because the same oracle demonstrably moves `presidio-chunk-local`, that is a measured
property of bounded retention rather than an instrument too blunt to see a difference.

**Reading rule:** do not quote a DeltaFrag from a context-scored or validating detector
(Presidio, Cloud DLP, Model Armor) without this oracle. Those score a fragment on what it
looks like, so the cut position changes the answer. A regex does not.

`fragmentation_strategy` in each report reads `exhaustive-2-part`, and
`limitations.method_limits` carries the split count, so a row states which oracle produced
it rather than leaving a reader to infer it.
