"""Gate 4: LLM Guard under the enumerating oracles, over the seeds its midpoint sweep used.

WHY THIS ROW WAS MISSING, and why the gap mattered.

`exhaustive-splits/README.md` states a reading rule: *do not quote a DeltaFrag from a
context-scored or validating detector without the enumerating oracle*. LLM Guard 0.3.16
pins `presidio-analyzer==2.2.358` and adds a transformer NER on top, so it is exactly such
a detector -- and both of its rows were midpoint-only. The manuscript therefore had to label
its LLM Guard DeltaFrag values "explicitly midpoint-only", which is honest and is also the
paper conceding a measurement it could have made.

WHAT MAKES IT EXPENSIVE, stated because it is the reason for every scope decision here.
LLM Guard runs a DeBERTa NER per scanned delta on CPU. Its published midpoint rows measure
1,408 ms mean per request chunk-local and 796 ms buffered. So:

    exhaustive-2-part   252 requests/seed  ~6 min chunk-local, ~3 min buffered
    union-worst-case  2,026 requests/seed  ~47 min chunk-local, ~27 min buffered

Six seeds of the two-part oracle is under an hour and is run. Six seeds of the union oracle
is seven hours and is NOT run: the union stage covers the published seed only, and the
omission is a declared resource cap rather than an approximation.

THE ATTRIBUTION IS EXPLICIT AND DOES NOT MOVE. `benchmarks/llm-guard-v2-profile/gateway.py`
is a study-owned wrapper that makes ONE decision -- scan each delta, or accumulate and scan
once -- because `Sensitive.scan` and `Deanonymize.scan` both take the complete model output
and the published API offers no third option. These rows measure that integration choice.
They are not a claim about a vendor streaming product, and LLM Guard does not ship one.

THE READ DEADLINE IS RAISED ON PURPOSE. The harness default is 10 s so a stalled socket
costs one case rather than the sweep. LLM Guard's p95 is 1.8 s and this machine is running
other measurements, so 10 s is close enough to be reached by contention rather than by the
target -- and a contended timeout would be recorded as an inconclusive case, i.e. as a
property of the gateway. 45 s keeps the guard while putting it out of reach of scheduling
noise.

PRECONDITION, and it is the gotcha that cost this project a day: `LLMGUARD_MODE` is read at
start-up and is invisible from outside the process. 8790 must have been started
`chunk-local` and 8792 `buffered`. Both gateways print their mode on the first line of their
own log; check that, do not assume. If a listener's start-up cannot be accounted for,
restart it rather than measure it.

Usage:
    python benchmarks/llm_guard_exhaustive.py twopart
    python benchmarks/llm_guard_exhaustive.py union
    python benchmarks/llm_guard_exhaustive.py one llm-guard-buffered 0000000000000003
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGING = ROOT / "benchmarks" / "results" / "staging-llm-guard"
PUBLISHED = (
    ROOT
    / "benchmarks"
    / "results"
    / "v2-response-split"
    / "exhaustive-llm-guard-seed-sweep"
)

# The six seeds the published `seed-sweep-llm-guard-*.json` files use. Matching them is
# what makes the oracle comparison a paired one rather than two unrelated samples.
SEEDS = [f"{i:016x}" for i in range(1, 7)]
PUBLISHED_SEED = "a1b2c3d4e5f60001"
CAPTURE_PORT = 8799

ROWS = (
    ("llm-guard-chunk-local", 8790),
    ("llm-guard-buffered", 8792),
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "pii-leak-benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    # See the module docstring: a contended timeout would be recorded as a property of the
    # gateway. The guard is kept, just moved out of reach of scheduling noise.
    env.setdefault("V2_CLIENT_READ_TIMEOUT", "45")
    env.setdefault("V2_CLIENT_CONNECT_TIMEOUT", "10")
    # Anonymize runs over the whole request body, so the request path IS configured.
    env["V2_REQUEST_PATH_REDACTION"] = "configured"
    return env


def _emit(policy: str, port: int, seed: str, oracle: str, out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable, "-m", "pii_leak_benchmark.v2_emitter", "--validate",
        "--only", policy,
        "--gateway-url", f"http://127.0.0.1:{port}/v1/chat/completions",
        "--upstream-port", str(CAPTURE_PORT),
        "--model", "capture",
        "--seed", seed,
        "--oracle", oracle,
        "--out", str(out),
    ]
    label = f"{policy} seed={seed} oracle={oracle}"
    print(f"\n=== {label} -> {out}", flush=True)
    started = time.perf_counter()
    result = subprocess.run(argv, cwd=ROOT, env=_env(), check=False)
    print(f"--- {label}: exit {result.returncode} in {time.perf_counter() - started:.0f}s",
          flush=True)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed")


def stage_twopart() -> None:
    for seed in SEEDS:
        for policy, port in ROWS:
            _emit(policy, port, seed, "exhaustive-2-part",
                  STAGING / "exhaustive-2-part" / seed)


def stage_union() -> None:
    """Published seed only. Six seeds of this oracle is about seven hours on this target."""
    for policy, port in ROWS:
        _emit(policy, port, PUBLISHED_SEED, "union-worst-case",
              STAGING / "worst-case" / PUBLISHED_SEED)


def stage_one(policy: str, seed: str) -> None:
    """Run one recoverable two-part row without repeating completed expensive rows."""
    ports = dict(ROWS)
    if policy not in ports:
        raise SystemExit(f"unknown policy {policy!r}; known: {sorted(ports)}")
    if seed not in SEEDS:
        raise SystemExit(f"seed must be one of the six systematic seeds: {SEEDS}")
    _emit(policy, ports[policy], seed, "exhaustive-2-part",
          STAGING / "exhaustive-2-part" / seed)


def publishable_llm_guard_files(
    root: pathlib.Path = STAGING / "exhaustive-2-part",
) -> list[pathlib.Path]:
    return sorted(
        path
        for path in root.rglob("*.json")
        if not path.name.endswith(".summary.json")
    )


def stage_promote() -> None:
    """Archive raw reports inside the v2 profile tree under the same relative layout."""
    import shutil

    source_root = STAGING / "exhaustive-2-part"
    paths = publishable_llm_guard_files(source_root)
    if not paths:
        raise SystemExit(f"nothing staged at {STAGING}")
    for path in paths:
        target = PUBLISHED / path.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    print(f"promoted {len(paths)} LLM Guard exhaustive files into {PUBLISHED}")


STAGES = {"twopart": stage_twopart, "union": stage_union, "promote": stage_promote}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "one":
        if len(argv) != 3:
            raise SystemExit("usage: llm_guard_exhaustive.py one <policy> <seed>")
        stage_one(argv[1], argv[2])
        return 0
    for name in argv:
        if name not in STAGES:
            raise SystemExit(f"unknown stage {name!r}; known: {sorted(STAGES)}")
        STAGES[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
