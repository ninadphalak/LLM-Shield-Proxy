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
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pii-leak-benchmark"))

from pii_leak_benchmark.artifact import write_json_artifact  # noqa: E402
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    DEFAULT_POLICIES,
    run_policy,
)

METRICS = ("fidelity_rate", "leak_single_chunk", "leak_adversarial", "delta_frag")


def sweep(
    policies: list[str],
    seeds: list[str],
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in policies:
        rows = []
        for seed in seeds:
            _report, summary = run_policy(
                name,
                seed=seed,
                gateway_url=gateway_url,
                upstream_port=upstream_port,
                model=model,
            )
            rows.append({
                "seed": seed,
                **{m: summary[m] for m in METRICS},
                "inconclusive": summary["inconclusive"],
                "echo_observable": summary["echo_observable"],
                # THE DENOMINATOR OF THE FOUR RATES ABOVE. This row used to carry
                # `"cases": summary["cases"]`, which was `metrics.cases_scored`, which is
                # `len(results)` -- the cases ATTEMPTED. A reader of `seed-sweep.json`
                # reconstructing a leak count from `leak_adversarial x cases` got the
                # wrong number by exactly the inconclusive count, and this file is the one
                # the README calls "the numbers to cite".
                "cases_applicable": summary["cases_applicable"],
                "cases_attempted": summary["cases_attempted"],
                # The four rates do not say whether the run PASSED. A request-path leak
                # produces `fail` with all four response rates at 0.00, which is what
                # LiteLLM does, so a sweep that records only the rates hides it.
                "outcome": _report["outcome"],
                "request_path_leak": _report["checks"]["configured_upstream_boundary"][
                    "leaked_entity_types"
                ],
            })
            if summary["inconclusive"] >= summary["cases_attempted"]:
                # Every case refused. The four rates are all 0.00, which reads as a
                # flawless gateway, so refuse to record it as a result at all.
                #
                # Compared against cases_ATTEMPTED, deliberately. Against
                # `cases_applicable` this reads `inconclusive >= applicable`, which the
                # two numbers satisfy whenever HALF the array dies -- 16 >= 16 -- and the
                # sweep would abort on a partially-degraded target that still produced a
                # publishable row. The guard's question is "did anything come back at
                # all", and only the attempted total can answer it.
                raise SystemExit(
                    f"{name} seed={seed}: all {summary['cases_attempted']} cases "
                    "inconclusive -- the target answered nothing. Check the container is "
                    "running and owns the port before trusting any row."
                )
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
            # WHICH INSTRUMENT PRODUCED THESE NUMBERS. The README calls the sweep files
            # "the numbers to cite", and until now they carried no way to tell that the
            # leak inspector had changed underneath them -- the single-run artefacts have
            # `inspection_scope` and are guarded on it, the sweeps had nothing. Both
            # scopes are generated from capability registries, so these digests change
            # exactly when the inspector's declared reach does.
            "instrument": _instrument(),
        }
    return out


def _instrument() -> dict[str, str]:
    """The emitter's own provenance block, not a second copy of it.

    This used to rebuild the digests here from the two scope strings. A sweep and a
    single-run artefact could therefore disagree about what "the current instrument"
    means, which is the drift the block exists to detect, one level up.
    """
    from pii_leak_benchmark.v2_emitter import instrument_block

    return instrument_block()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default="benchmarks/results/v2-response-split/seed-sweep.json")
    parser.add_argument("--gateway-url", default=None)
    parser.add_argument("--upstream-port", type=int, default=0)
    parser.add_argument("--model", default="test")
    # THE CLIENT DEADLINE, exposed because the right value depends on the target. The
    # harness bounds every request at connect=5s / read=10s so a stalled socket costs one
    # case instead of the sweep -- 12 seeds x 32 cases x the old `urlopen(timeout=120)` is
    # long enough that one stalled seed is indistinguishable from a hang. A gateway that
    # loads a transformer model on its first request (LLM Guard, NeMo) can legitimately
    # exceed ten seconds; raise it for those rather than letting the sweep record a
    # container that was merely slow as an inconclusive case.
    parser.add_argument("--connect-timeout", type=float, default=None)
    parser.add_argument("--read-timeout", type=float, default=None)
    args = parser.parse_args(argv)

    from pii_leak_benchmark import v2_emitter

    if args.connect_timeout is not None:
        v2_emitter.CLIENT_CONNECT_TIMEOUT = args.connect_timeout
    if args.read_timeout is not None:
        v2_emitter.CLIENT_READ_TIMEOUT = args.read_timeout
    print(
        f"client deadline: connect={v2_emitter.CLIENT_CONNECT_TIMEOUT}s "
        f"read={v2_emitter.CLIENT_READ_TIMEOUT}s",
        flush=True,
    )

    # Local policies only unless asked by name: the cloud rows are billed per delta.
    policies = [n.strip() for n in args.only.split(",") if n.strip()] or list(DEFAULT_POLICIES)
    seeds = [f"{i:016x}" for i in range(1, args.seeds + 1)]

    print(f"sweeping {len(policies)} policies x {len(seeds)} seeds", flush=True)
    results = sweep(
        policies,
        seeds,
        gateway_url=args.gateway_url,
        upstream_port=args.upstream_port,
        model=args.model,
    )

    # The README calls this file "the numbers to cite", so its bytes must not depend on
    # which host produced it.
    write_json_artifact(args.out, results, indent=1)

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
