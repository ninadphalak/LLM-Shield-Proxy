# The same nine policies, scored by the exhaustive split oracle

Produced with `--exhaustive-splits`, which cuts each adversarial value at **every internal
offset** instead of once at its midpoint, and fails the case if **any** split leaks. Same
corpus, same seed (`a1b2c3d4e5f60001`), same inspector as the rows one directory up. The
only variable is where the value is cut: 236 internal adversarial splits over 16
adversarial cases, plus 16 uncut single-chunk requests, which is 252 captured requests in
total. Those three numbers are now emitted by the instrument in
`metrics.partition_oracle`, not only stated here.

These are in a subdirectory on purpose, so the fixed published-seed oracle comparison is
visibly distinct from the midpoint rows. The guards in
`tests/conformance/test_results_are_comparable.py` walk the parent tree recursively and
verify that both oracle directories use the current corpus and inspector.

The twelve-seed Presidio exhaustive result is archived separately under
`../exhaustive-presidio-seed-sweep/`.

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
the reports therefore state which oracle produced them rather than leaving a reader to
infer it.

**The `252 splits` erratum is fixed (round 7, 2026-09-09).** These reports used to call all
252 captured requests "splits" in `limitations.method_limits[4]`. That string is produced
inside `build_report`, which is part of the instrument digest, so correcting it moved
`inspector_sha256` and every report in this tree was re-measured. The reports now carry a
`metrics.partition_oracle` block that publishes the three numbers separately:

- `adversarial_partitions` — 236, the internal partitions actually driven
- `uncut_single_chunk_requests` — 16, the baseline arm, which splits nothing
- `captured_requests_total` — 252

Three places in each report describe the oracle: that block, the `fragmentation_strategy`
enum, and the `method_limits` sentence. `tests/conformance/test_published_profiles.py`
fails if any two of them disagree, so the erratum cannot come back as a prose edit.

**The stronger oracle now has a directory of its own.** `../worst-case-splits/` carries the
same policies under the union of every internal two-part split and every internal
three-part partition. See its README for what the third piece changes and what it does not.
