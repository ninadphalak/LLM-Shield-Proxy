# The union oracle: every two-part AND every three-part partition

Produced with `--oracle union-worst-case`. Same corpus, same seed (`a1b2c3d4e5f60001`),
same inspector as the rows one and two directories up. The only thing that changes is how
many pieces the injected value is cut into, and where.

## What the oracle enumerates

For a rendered value of `N` characters:

| family | partitions | pieces |
|---|---|---|
| `exhaustive-2-part` | `N - 1` | 2 |
| `exhaustive-3-part` | `choose(N - 1, 2)` | 3 |
| `union-worst-case` | both together | 2 or 3 |

Every partition is checked before it is sent: the pieces concatenate back to the value
byte for byte, none is empty, and no piece contains the whole value. A partition that
failed any of those would not be measuring fragmentation.

A case fails a family if **any** partition in that family leaks. The union statistic is
the case rate under both families together, so it can never be below either one.

## What "worst case" means here, and what it does not

It is the worst case over **the values in this corpus** and **these two partition
families**. It is not:

- a worst case over arbitrary streams,
- a worst case over arbitrary values,
- a worst case over interleavings with other content,
- or a worst case over partitions into more than three pieces.

Each report says so in `metrics.partition_oracle.worst_case.definition`, so the number
cannot be quoted without its bound.

## Resource cap

Three-part enumeration grows quadratically. The cap is 6,000 partitions per case per
family, published in every report as
`metrics.partition_oracle.resource_cap_per_case_per_family`.

A family that would exceed the cap is **not shortened**. The case is marked capped,
excluded from that family's denominator, and counted in `cases_capped`. An enumeration
that was abandoned must never be scored as a target that held. No case in this corpus hits
the cap; the longest value here needs 253 three-part partitions.

The four Google Cloud rows are **not** in this directory. Union enumeration there is about
1,900 billed detector calls per row, and the omission is a declared cap rather than an
approximation. They are measured at the midpoint and at every internal two-part split.

## Published-seed result

The union drove 2,010 adversarial partitions over 16 adversarial cases: 236 two-part
splits and 1,774 three-part partitions. The matching baseline used 16 uncut requests, for
2,026 captured requests per policy. No case hit the cap and no transport case was
inconclusive.

| policy | single-chunk leak | two-part leak | three-part leak | union leak | union DeltaFrag |
|---|---:|---:|---:|---:|---:|
| `chunk-local` | 0.125 | 1.000 | 1.000 | 1.000 | 0.875 |
| `bounded-retention` | 0.125 | 0.125 | 0.125 | 0.125 | 0.000 |
| `retention-plus-decoding` | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `presidio-chunk-local` | 0.125 | 1.000 | 1.000 | 1.000 | 0.875 |
| `presidio-retention` | 0.125 | 0.125 | 0.125 | 0.125 | 0.000 |

The third piece changes no case verdict in these five rows. That is a measured result for
this corpus, not a reason to omit the family: it establishes that the earlier two-part
finding already reached the union statistic here, while both retention controls remain at
their uncut baselines.

## Reproduce

Run from the repository root with the Presidio analyzer listening on `127.0.0.1:5002`.
`--validate` writes reports, so always pass an explicit `--out`.

```bash
PYTHONPATH=pii-leak-benchmark python -m pii_leak_benchmark.v2_emitter --validate \
  --only presidio-chunk-local,presidio-retention --oracle union-worst-case \
  --seed a1b2c3d4e5f60001 --out /tmp/worst-case
```

The twelve-seed version of the same oracle is archived under
`../worst-case-presidio-seed-sweep/`.
