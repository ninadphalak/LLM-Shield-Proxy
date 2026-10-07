# Presidio under the two-part plus three-part union oracle, 12 seeds

This directory holds 24 raw reports: `presidio-chunk-local` and `presidio-retention` over
the twelve systematic seeds `0000000000000001` through `000000000000000c`. It uses the
same seeds as the midpoint and exhaustive-two-part studies, so the oracle comparison is
paired rather than a comparison of unrelated generated values.

“Worst case” is bounded here. Each adversarial case enumerates every internal two-part
split and every internal three-part partition of the measured value. It does not cover
arbitrary values, interleavings, streams, or partitions into more than three pieces.

## Result

Every report drove 2,010 adversarial partitions and 16 uncut baseline requests. No report
hit the 6,000-per-family cap, and no case was inconclusive.

| policy | seeds | mean single | mean union adversarial | mean DeltaFrag | DeltaFrag range |
|---|---:|---:|---:|---:|---:|
| `presidio-chunk-local` | 12 | 0.1667 | 1.0000 | 0.8333 | 0.625–0.875 |
| `presidio-retention` | 12 | 0.1615 | 0.1615 | 0.0000 | 0.000–0.000 |

The result is identical to the every-two-part-split study on the same seeds: every value
set has a leaking two-part split, adding every three-part partition changes no case
verdict, and retention stays at the single-chunk baseline. The union result therefore
confirms rather than enlarges the measured two-part effect for this corpus.

The hand-picked publication seed is deliberately not a thirteenth observation in this
summary. Its point reproduction is in `../worst-case-splits/`.

## Reproduce

Run from the repository root with Presidio listening on `127.0.0.1:5002`:

```bash
PYTHONPATH=pii-leak-benchmark python benchmarks/refresh_v2_evidence.py worstcase-presidio
```

The command writes to `benchmarks/results/staging-refresh/`. Compare and promote the
staging tree explicitly; it does not overwrite this directory by itself.
