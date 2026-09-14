# Presidio exhaustive split sweep

This directory archives the `--exhaustive-splits` Presidio runs used for evidence round 6.
The twelve numbered directories use exactly the seeds in the midpoint
`../seed-sweep.json`; the `a1b2c3d4e5f60001/` directory is a separate re-run of the
published seed. Each directory contains both study-owned wrappers around the same live
Presidio analyzer:

- `presidio-chunk-local.json`
- `presidio-retention.json`

All 26 reports carry corpus digest
`30efa2eb658888448b4416bb527c9ff5ea8489a469f0bec71049eb88ac9efd3c`, inspector digest
`3ac1f621aa008d04`, `fragmentation_strategy: exhaustive-2-part`, FidelityRate 1.00, and
zero inconclusive cases. The published-seed re-run reproduces the headline metrics and
case digest in the matching reports under `../exhaustive-splits/`.

## Twelve-seed result

Cells are `mean [min-max]` over the twelve numbered seed directories. Each per-seed rate
uses 16 applicable cases per fragmentation arm.

| Wrapper and oracle | LeakRate, single | LeakRate, adversarial | DeltaFrag |
|---|---:|---:|---:|
| chunk-local, midpoint | 0.1667 [0.125-0.375] | 0.7188 [0.25-1.00] | 0.5521 [-0.125-0.875] |
| chunk-local, every internal split | 0.1667 [0.125-0.375] | **1.00 [1.00-1.00]** | **0.8333 [0.625-0.875]** |
| retention, midpoint | 0.1615 [0.125-0.375] | 0.1615 [0.125-0.375] | 0.00 [0.00-0.00] |
| retention, every internal split | 0.1615 [0.125-0.375] | 0.1615 [0.125-0.375] | **0.00 [0.00-0.00]** |

Enumeration removes the chunk-local adversarial LeakRate's seed variation: the midpoint
range is 0.25-1.00, whereas every enumerated seed is exactly 1.00. Retention keeps
DeltaFrag at exactly zero on every seed under both oracles. The seed changes only the
generated synthetic values; it does not replicate machines, configurations, case order,
or case structure.

## Reproduce

Run from the repository root with the Presidio analyzer listening on `127.0.0.1:5002`.
`--validate` writes reports, so keep the explicit scratch output directory.

```powershell
$env:PYTHONPATH = 'pii-leak-benchmark'
python -m pii_leak_benchmark.v2_emitter --validate `
  --only presidio-chunk-local,presidio-retention --exhaustive-splits `
  --seed 0000000000000001 --out $env:TEMP\presidio-exhaustive\0000000000000001
```

Repeat for seeds `0000000000000001` through `000000000000000c`. Each exhaustive report
executes 236 internal adversarial split attempts plus 16 uncut single-chunk requests, or
252 captured requests total.

**The `252 splits` erratum is fixed (round 7, 2026-09-09).** These reports used to call all
252 captured requests "splits". The instrument now publishes the three numbers separately
in `metrics.partition_oracle`, and a test fails if the block, the `fragmentation_strategy`
enum and the `method_limits` sentence disagree. Fixing that string moved
`inspector_sha256`, so all 26 reports here were re-measured; every rate above reproduced.

**A stronger oracle is archived beside this one.** `../worst-case-presidio-seed-sweep/`
runs the same twelve seeds under the union of every internal two-part split and every
internal three-part partition, and reports the union statistic with its own bound stated
in the artefact.
