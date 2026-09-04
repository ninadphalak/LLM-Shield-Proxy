"""Multi-seed sweep of the v2 response-split profile.

A single seed is not a result. The fixture values are drawn per seed, and whether a
detector fires on a fragment depends on the value -- an email split at one offset may
leave a still-valid address on the right-hand side, and a different draw may not. The
first fixed-seed run of this profile moved `presidio-chunk-local` from DeltaFrag 1.0 to
0.3333 purely by changing the seed, which is exactly the kind of single-run number this
project's reporting rules forbid publishing.

So: run every policy over N seeds and report the distribution. Seeds are recorded, so any
row can be reproduced with `--seed`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import POLICIES, run_policy  # noqa: E402

METRICS = ("fidelity_rate", "leak_single_chunk", "leak_adversarial", "delta_frag")


def sweep(policies: list[str], seeds: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in policies:
        rows = []
        for seed in seeds:
            _report, summary = run_policy(name, seed=seed)
            rows.append({"seed": seed, **{m: summary[m] for m in METRICS}})
            print(
                f"  {name:24} seed={seed}  "
                + "  ".join(f"{m}={rows[-1][m]}" for m in METRICS),
                flush=True,
            )
        out[name] = {
            "runs": rows,
            "summary": {
                m: {
                    "mean": round(statistics.fmean(r[m] for r in rows), 4),
                    "min": min(r[m] for r in rows),
                    "max": max(r[m] for r in rows),
                    "stdev": round(statistics.stdev([r[m] for r in rows]), 4)
                    if len(rows) > 1
                    else 0.0,
                }
                for m in METRICS
            },
            "seeds": seeds,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default="benchmarks/results/v2-response-split/seed-sweep.json")
    args = parser.parse_args(argv)

    policies = [n.strip() for n in args.only.split(",") if n.strip()] or list(POLICIES)
    seeds = [f"{i:016x}" for i in range(1, args.seeds + 1)]

    print(f"sweeping {len(policies)} policies x {len(seeds)} seeds", flush=True)
    results = sweep(policies, seeds)

    Path(args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"{'policy':<26}{'fidelity':>18}{'leak(1chunk)':>18}{'leak(adv)':>18}{'DeltaFrag':>18}")
    print("-" * 98)
    for name, block in results.items():
        cells = []
        for m in METRICS:
            st = block["summary"][m]
            cells.append(f"{st['mean']:.2f} [{st['min']:.2f}-{st['max']:.2f}]".rjust(18))
        print(f"{name:<26}" + "".join(cells))
    print(f"\nseeds: {len(seeds)}, written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
