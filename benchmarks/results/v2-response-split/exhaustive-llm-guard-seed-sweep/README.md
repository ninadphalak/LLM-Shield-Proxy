# LLM Guard under every internal two-part split, 6 seeds

This directory holds 12 raw reports from LLM Guard 0.3.16: the study-owned chunk-local
and whole-response-buffered wrappers over the same six systematic seeds as their midpoint
sweeps. LLM Guard supplies the detector, anonymisation vault, and deanonymisation API; the
choice to scan each delta or to buffer the whole response belongs to this study wrapper.
These rows are not a vendor streaming-product claim.

## Result

| wrapper | seeds | Fidelity mean [range] | single leak mean [range] | enumerated leak mean [range] | DeltaFrag mean [range] |
|---|---:|---:|---:|---:|---:|
| chunk-local | 6 | 1.0000 [1.0000–1.0000] | 0.1667 [0.0000–0.2500] | 1.0000 [1.0000–1.0000] | 0.8333 [0.7500–1.0000] |
| whole-response buffered | 6 | 0.9974 [0.9844–1.0000] | 0.2917 [0.2500–0.3125] | 0.2917 [0.2500–0.3125] | 0.0000 [0.0000–0.0000] |

Every published report has 32 applicable cases, zero inconclusive cases, and 236 internal
adversarial splits plus 16 uncut baseline requests. Enumeration changes the chunk-local
result materially: its midpoint adversarial leak mean was 0.7500 and mean DeltaFrag was
0.5833; under all two-part splits every generated value set has a leaking split,
adversarial leakage is 1.0000 on all six seeds, and mean DeltaFrag is 0.8333. Buffering
holds the fragmented rate at the single-chunk baseline, but it is whole-response delivery,
not incremental streaming.

No union-oracle LLM Guard report is claimed. Six seeds of the two-part oracle took roughly
an hour on CPU; adding every three-part partition was estimated at about seven hours and
is a declared resource cap.

## Transport retries are recorded

The first buffered seed-3 attempt completed with 29 of 32 cases applicable and three
transport-inconclusive cases, moving DeltaFrag slightly negative. It was preserved before
one bounded retry; that retry produced the published 32/32 row above. Earlier in the same
campaign, the chunk-local service exited with Windows `WinError 10055` after the preceding
FIDE union stage had opened tens of thousands of loopback connections. The harness rejected
the resulting all-refused seed-4 report as schema-invalid. After TIME_WAIT drained, the
listener was restarted in verified `chunk-local` mode and only the missing row was rerun.
Neither invalid nor partial report is published.

## Reproduce

Verify the two wrapper logs state `chunk-local` on port 8790 and `buffered` on port 8792,
then run from the repository root:

```bash
PYTHONPATH=pii-leak-benchmark python benchmarks/llm_guard_exhaustive.py twopart
```

For recovery after a service failure, rerun one missing row without repeating completed
measurements:

```bash
PYTHONPATH=pii-leak-benchmark python benchmarks/llm_guard_exhaustive.py one \
  llm-guard-buffered 0000000000000003
```

Both commands write to `benchmarks/results/staging-llm-guard/`; promotion is a separate
explicit step.
