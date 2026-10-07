"""Re-measure every v2 artefact on the current instrument, into staging, then compare.

WHY A COMPLETE REFRESH RATHER THAN A STRING EDIT.

`limitations.method_limits[4]` published "252 splits over 16 adversarial cases" for every
exhaustive row in the tree. 252 is not the split count: it is 236 internal adversarial
partitions plus the 16 uncut single-chunk requests that form the baseline arm. The string
is produced inside `build_report`, which is inside `_INSTRUMENTED`, so correcting it moves
`inspector_sha256` and marks all 54 reports and nine sweeps stale.

The tempting alternatives were both rejected in
`.llm/research/ieee-software/fide/DESIGN-DECISION.md`: taking `build_report` out of the
digest, or moving the sentence into a function the digest does not cover. Both are ways of
editing published evidence without admitting it, and the second is the first with an extra
indirection. The rule this repository already states is that a stale row is worse than a
missing one, and a CORRECTED row that was never re-measured is worse than either, because
it looks freshly measured and is not.

WHAT THIS SCRIPT ENFORCES.

  * `--out` is always explicit and always a STAGING directory. `--validate` on the emitter
    WRITES, and its `--out` defaults to the published directory, so an invocation without
    it overwrites the artefacts it was meant to reproduce. That has happened here.
  * Nothing is promoted automatically. `compare` prints every headline number that moved,
    and a moved number is a finding that needs a sentence, not a silent overwrite.
  * The billed stage is opt-in by name and never runs as part of `all`.

STAGES, cheapest first:

    local        5 reference + 2 Presidio rows, midpoint            ~4 min
    exhaustive   the same locals under exhaustive-2-part            ~6 min
    presidio     26 Presidio exhaustive rows across 13 seeds        ~15 min
    sweep        seed-sweep.json, 7 policies x 12 seeds             ~50 min
    gateways     the eight external rows (needs the listeners up)   ~15 min
    gatesweeps   the eight per-target sweeps, 6 seeds each          ~60 min
    gcp          the four cloud rows, midpoint and exhaustive       BILLED

Usage:
    python benchmarks/refresh_v2_evidence.py local exhaustive presidio
    python benchmarks/refresh_v2_evidence.py compare
    python benchmarks/refresh_v2_evidence.py promote      # only after reading compare
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))

from pii_leak_benchmark.artifact import write_json_artifact  # noqa: E402

PUBLISHED = ROOT / "benchmarks" / "results" / "v2-response-split"
STAGING = ROOT / "benchmarks" / "results" / "staging-refresh"

PUBLISHED_SEED = "a1b2c3d4e5f60001"
SWEEP_SEEDS = [f"{i:016x}" for i in range(1, 13)]

LOCAL_POLICIES = [
    "passthrough",
    "redact-all",
    "chunk-local",
    "bounded-retention",
    "retention-plus-decoding",
    "presidio-chunk-local",
    "presidio-retention",
]
# Exactly the nine rows in `exhaustive-splits/`, minus the four billed cloud ones, which
# have their own stage.
EXHAUSTIVE_LOCAL = [
    "chunk-local",
    "bounded-retention",
    "retention-plus-decoding",
    "presidio-chunk-local",
    "presidio-retention",
]
GCP_POLICIES = [
    "gcp-dlp-chunk-local",
    "gcp-dlp-retention",
    "gcp-model-armor-chunk-local",
    "gcp-model-armor-retention",
]

# The headline numbers. A move in any of these is a result, not a refresh artefact.
HEADLINES = (
    ("metrics", "fidelity_rate"),
    ("metrics", "leak_rate", "single_chunk"),
    ("metrics", "leak_rate", "adversarial"),
    ("metrics", "leak_rate", "overall"),
    ("metrics", "delta_frag"),
    ("metrics", "cases_applicable"),
    ("metrics", "cases_inconclusive"),
    ("metrics", "cases_echo_observable"),
    ("metrics", "detector_blind_entities"),
    ("corpus", "sha256"),
    ("cases_digest",),
    ("outcome",),
    ("passed",),
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "pii-leak-benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(argv: list[str], label: str, extra_env: dict[str, str] | None = None) -> None:
    print(f"\n=== {label}", flush=True)
    started = time.perf_counter()
    env = _env()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    print(f"--- {label}: exit {result.returncode} in {time.perf_counter() - started:.0f}s",
          flush=True)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed; refusing to continue with a partial refresh")


def _emit(policies: list[str], out: pathlib.Path, seed: str, oracle: str | None = None,
          extra: list[str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable, "-m", "pii_leak_benchmark.v2_emitter", "--validate",
        "--only", ",".join(policies), "--seed", seed, "--out", str(out),
    ]
    if oracle:
        argv += ["--oracle", oracle]
    argv += extra or []
    _run(argv, f"{','.join(policies)} seed={seed} oracle={oracle or 'midpoint'} -> {out}")


def stage_local() -> None:
    _emit(LOCAL_POLICIES, STAGING, PUBLISHED_SEED)


def stage_exhaustive() -> None:
    _emit(EXHAUSTIVE_LOCAL, STAGING / "exhaustive-splits", PUBLISHED_SEED,
          oracle="exhaustive-2-part")


# SEEDS RUN IN PARALLEL, and it is safe for a specific reason rather than by hope.
#
# Each emitter process binds its capture and gateway to EPHEMERAL ports (`--upstream-port`
# defaults to 0), so two runs cannot collide on an address -- which is the failure the
# `allow_reuse_address = False` guard and the capture self-probe exist to catch, and both
# still fire per process. The Presidio analyzer is an HTTP service whose answers do not
# depend on how many callers it has, so concurrency changes latency and not verdicts.
#
# LATENCY IS THE ONE THING IT DOES CHANGE. `checks.client_observed_latency` is published,
# and under a parallel run it measures a contended machine. That field is already declared
# as "loopback and in-process; not gateway overhead on a network", and the refresh
# comparison deliberately excludes it from the headline set. It is recorded here so a
# reader who compares round 7 timings with round 6 timings knows why they differ.
PARALLEL_SEEDS = int(os.environ.get("V2_REFRESH_JOBS", "3"))


def _emit_seeds(policies: list[str], subdir: str, seeds: list[str], oracle: str) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def one(seed: str) -> None:
        _emit(policies, STAGING / subdir / seed, seed, oracle=oracle)

    if PARALLEL_SEEDS <= 1:
        for seed in seeds:
            one(seed)
        return
    with ThreadPoolExecutor(max_workers=PARALLEL_SEEDS) as pool:
        # list() so an exception in any seed propagates rather than being swallowed by a
        # generator nobody consumed. A partial refresh must fail loudly.
        list(pool.map(one, seeds))


def stage_presidio() -> None:
    """The 12 systematic seeds plus the published seed, both Presidio wrappers."""
    _emit_seeds(["presidio-chunk-local", "presidio-retention"],
                "exhaustive-presidio-seed-sweep", SWEEP_SEEDS + [PUBLISHED_SEED],
                "exhaustive-2-part")


def stage_worstcase() -> None:
    """The union oracle at the published seed, into its own directory.

    A SEPARATE DIRECTORY, not a second file beside the two-part rows. The oracle is part of
    what a row measured: a union row and a two-part row of the same policy at the same seed
    are different measurements and filing them together invites exactly the comparison
    nobody should make without saying which oracle produced which number.
    """
    _emit(EXHAUSTIVE_LOCAL, STAGING / "worst-case-splits", PUBLISHED_SEED,
          oracle="union-worst-case")


def stage_worstcase_presidio() -> None:
    """The union oracle across the same twelve generated value sets as the two-part sweep.

    This is the evidence the brief requires BEFORE the secret family may be said to
    generalise the failure: the three-part and union oracles applied to the study that
    already exists, on the same seeds, so the new statistic can be compared with the old
    one rather than only with itself.
    """
    _emit_seeds(["presidio-chunk-local", "presidio-retention"],
                "worst-case-presidio-seed-sweep", SWEEP_SEEDS, "union-worst-case")


def stage_sweep() -> None:
    """`seed-sweep.json`, 7 policies x 12 seeds, run one policy per process and merged.

    WHY MERGE RATHER THAN RUN IT AS ONE SWEEP. `v2_seed_sweep.py` walks policies and seeds
    sequentially, which is 84 corpus runs and about 50 minutes. Each policy's block in the
    output file is independent of every other -- the file is a dict keyed by policy name --
    so running them as separate processes and merging produces the same document in a
    third of the time.

    WHAT IS PRESERVED, deliberately: the key order. `seed-sweep.json` is the file the
    README calls "the numbers to cite", and a reordered document produces a diff that
    looks like a rewrite. Blocks are merged in `LOCAL_POLICIES` order, which is the order
    a single-process sweep produces.

    WHAT IS NOT PARALLELISED: the seeds inside one policy. Each policy's twelve runs stay
    in one process and in order, so a policy's block is produced exactly as it would be by
    the unparallelised script.
    """
    from concurrent.futures import ThreadPoolExecutor

    STAGING.mkdir(parents=True, exist_ok=True)
    parts = STAGING / "_sweep-parts"
    parts.mkdir(exist_ok=True)

    def one(policy: str) -> pathlib.Path:
        target = parts / f"{policy}.json"
        _run(
            [sys.executable, "benchmarks/v2_seed_sweep.py", "--seeds", "12",
             "--only", policy, "--out", str(target)],
            f"seed-sweep: {policy} x 12 seeds",
        )
        return target

    with ThreadPoolExecutor(max_workers=PARALLEL_SEEDS) as pool:
        list(pool.map(one, LOCAL_POLICIES))

    merged: dict[str, Any] = {}
    for policy in LOCAL_POLICIES:
        block = json.loads((parts / f"{policy}.json").read_text(encoding="utf-8"))
        if policy not in block:
            raise SystemExit(f"{policy}: its sweep file does not contain its own block")
        merged[policy] = block[policy]
    write_json_artifact(STAGING / "seed-sweep.json", merged, indent=1)
    print(f"\nmerged {len(merged)} policy blocks into seed-sweep.json", flush=True)


def stage_gcp() -> None:
    """BILLED. Four cloud rows at the midpoint and again under exhaustive-2-part.

    Three-part enumeration is NOT run here and the omission is deliberate and published:
    it would be roughly 1,900 billed detector calls per row. The reports carry the cap.
    """
    print("\n*** BILLED STAGE: Google Cloud DLP and Model Armor ***", flush=True)
    _emit(GCP_POLICIES, STAGING, PUBLISHED_SEED)
    _emit(GCP_POLICIES, STAGING / "exhaustive-splits", PUBLISHED_SEED,
          oracle="exhaustive-2-part")


def stage_gateways() -> None:
    _run(
        ["bash", "benchmarks/rerun-external-gateway-rows.sh"],
        "eight external gateway rows",
        # The shell runner's default is staging-external.  Keep this driver self-contained:
        # every stage it owns must populate the one tree `compare` and `promote` inspect.
        {"OUT": str(STAGING)},
    )


def stage_gatesweeps() -> None:
    """The eight external six-seed sweeps, serialised around fixed capture port 8799."""
    _run(
        [sys.executable, "benchmarks/refresh_gateway_sweeps.py"],
        "eight external gateway seed sweeps",
    )


# `_sweep-parts/` holds one file per policy from the parallelised sweep. They are inputs to
# the merge, not artefacts, and promoting them would put seven single-policy sweep files
# into the published tree beside the merged one -- seven more things a reader could cite.
INTERMEDIATE_DIRS = ("_sweep-parts",)


def _is_intermediate(path: pathlib.Path) -> bool:
    return any(part in INTERMEDIATE_DIRS for part in path.parts)


def _leaf(document: dict, path: tuple[str, ...]):
    node = document
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return "<missing>"
        node = node[key]
    return node


def compare() -> int:
    """Every headline number, staged against published. Moves are printed, not hidden."""
    if not STAGING.is_dir():
        raise SystemExit(f"nothing staged at {STAGING}")
    staged = sorted(
        p for p in STAGING.rglob("*.json")
        if not p.name.startswith("seed-sweep") and not _is_intermediate(p)
    )
    moved: list[str] = []
    missing: list[str] = []
    compared = 0
    for path in staged:
        rel = path.relative_to(STAGING)
        old_path = PUBLISHED / rel
        if not old_path.is_file():
            missing.append(str(rel))
            continue
        new = json.loads(path.read_text(encoding="utf-8"))
        old = json.loads(old_path.read_text(encoding="utf-8"))
        for field in HEADLINES:
            compared += 1
            a, b = _leaf(old, field), _leaf(new, field)
            if a != b:
                moved.append(f"{rel.as_posix()}  {'.'.join(field)}: {a!r} -> {b!r}")

    print(f"\ncompared {compared} headline leaves across {len(staged)} staged reports")
    if missing:
        print(f"\n{len(missing)} staged rows have no published counterpart (new rows):")
        for name in missing:
            print("  +", name)
    if moved:
        print(f"\n{len(moved)} HEADLINE NUMBERS MOVED. Each needs a sentence before promotion:")
        for line in moved:
            print("  !", line)
    else:
        print("\nno headline number moved: the refresh reproduces the tagged evidence exactly")

    # The sweeps carry their own summary statistics.
    for path in sorted(STAGING.glob("seed-sweep*.json")):
        old_path = PUBLISHED / path.name
        if not old_path.is_file():
            print(f"\n+ {path.name} (new)")
            continue
        new = json.loads(path.read_text(encoding="utf-8"))
        old = json.loads(old_path.read_text(encoding="utf-8"))
        diffs = []
        total = 0
        for policy, block in new.items():
            old_block = old.get(policy, {})
            for metric, stats in block.get("summary", {}).items():
                for key, value in stats.items():
                    total += 1
                    was = old_block.get("summary", {}).get(metric, {}).get(key)
                    if was != value:
                        diffs.append(f"{policy}.{metric}.{key}: {was!r} -> {value!r}")
        print(f"\n{path.name}: {total} summary statistics, {len(diffs)} moved")
        for line in diffs:
            print("  !", line)
    return 1 if moved else 0


def promote() -> None:
    """Copy staging over the published tree. Run `compare` and read it first."""
    import shutil

    count = 0
    for path in sorted(STAGING.rglob("*.json")):
        if _is_intermediate(path):
            continue
        target = PUBLISHED / path.relative_to(STAGING)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    print(f"promoted {count} files into {PUBLISHED}")


STAGES = {
    "local": stage_local,
    "exhaustive": stage_exhaustive,
    "presidio": stage_presidio,
    "worstcase": stage_worstcase,
    "worstcase-presidio": stage_worstcase_presidio,
    "sweep": stage_sweep,
    "gateways": stage_gateways,
    "gatesweeps": stage_gatesweeps,
    "gcp": stage_gcp,
}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for name in argv:
        if name == "compare":
            return compare()
        if name == "promote":
            promote()
            return 0
        if name == "all":
            # Deliberately excludes `gcp`: a stage that spends money is never implied.
            for stage in ("local", "exhaustive", "presidio", "worstcase",
                          "worstcase-presidio", "sweep", "gateways", "gatesweeps"):
                STAGES[stage]()
            continue
        if name not in STAGES:
            raise SystemExit(f"unknown stage {name!r}; known: {sorted(STAGES)} + compare/promote/all")
        STAGES[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
