"""Re-run the published fragmentation experiment and diff it against the published JSON.

This calibrates two study-owned reference policies: one
chunk-local inspector and one whitespace-retaining inspector, on the same corpus, at
the published seed, under the midpoint oracle. Both run against a loopback capture server
and reference gateway started by the harness. No external gateway, account or model.
It verifies the instrument, not the external-detector findings or a memory bound.

WHAT IT COMPARES, AND WHY IT COMPARES EVERYTHING.

The check is a full recursive diff of the regenerated report against the published one,
with an explicit ignore list, rather than a comparison of the four headline rates. A
headline-only check passes while the corpus digest, the inspector digest, the case
inventory or a per-axis marginal drifts underneath it, and those are exactly the fields
whose drift would invalidate the published rates without changing them.

`NONDETERMINISTIC` is an ignore list measured on this repository, not a guess: a
chunk-local and a bounded-retention run reproduced their published reports with these
eleven fields differing and nothing else. Each one records WHERE and WHEN the run
happened or timing observations. Timing reproducibility is not tested. Drift outside this list is a
finding, and the exit status says so.

The list was wrong when first written, and the way it was wrong is worth keeping. It was
measured on the workstation that produced the published reports, under the interpreter
that produced them, so the three `environment` fields could not appear in the diff: they
were identical by coincidence of host. The cross-platform CI matrix found them on the
first run. An ignore list derived from one machine can only ever be a lower bound, which
is the argument for the matrix rather than a single reproducing runner.

Usage:
    python benchmarks/reproduce_fragmentation.py
    python benchmarks/reproduce_fragmentation.py --out /tmp/repro
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
from typing import Any, Iterator

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))

from pii_leak_benchmark.artifact import write_json_artifact  # noqa: E402

PUBLISHED = ROOT / "benchmarks" / "results" / "v2-response-split"
PUBLISHED_SEED = "a1b2c3d4e5f60001"
POLICIES = ("chunk-local", "bounded-retention")
SUPPORTED_POLICIES = (*POLICIES, "presidio-chunk-local", "presidio-retention")
EVIDENCE_COMMIT = "6cbfee39af93909a0d3aba77622ea67af63f84c4"
MANIFEST = ROOT / "benchmarks" / "evidence-round-8.manifest.json"

# Report paths that cannot reproduce because they record when, where and how fast the run
# went, not what it measured. Every other leaf must match the published one exactly.
#
# The `environment` block is provenance about the host. A reproduction on a different
# machine SHOULD disagree with it; a reproduction that agreed would mean the report was
# not recording the host that produced it.
NONDETERMINISTIC = frozenset(
    {
        ".generated_at",
        ".environment.python",
        ".environment.implementation",
        ".environment.platform",
        ".capture.self_probe.url",  # ephemeral loopback port
        ".capture.self_probe.round_trip_ms",
        ".checks.client_observed_latency.mean",
        ".checks.client_observed_latency.p50",
        ".checks.client_observed_latency.p95",
        ".checks.client_observed_latency.p99",
        ".metrics.partition_oracle.partition_seconds_total",
    }
)

HEADLINES = (
    ("Fidelity", ("metrics", "fidelity_rate")),
    ("Leak, single chunk", ("metrics", "leak_rate", "single_chunk")),
    ("Leak, fragmented", ("metrics", "leak_rate", "adversarial")),
    ("DeltaFrag", ("metrics", "delta_frag")),
    ("Cases applicable", ("metrics", "cases_applicable")),
    ("Cases inconclusive", ("metrics", "cases_inconclusive")),
    ("Corpus digest", ("corpus", "sha256")),
    ("Inspector digest", ("instrument", "inspector_sha256")),
    ("Outcome", ("outcome",)),
)


def _dig(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return "<missing>"
        node = node[key]
    return node


def _diff(published: Any, produced: Any, path: str = "") -> Iterator[tuple[str, Any, Any]]:
    if path in NONDETERMINISTIC:
        return
    if type(published) is not type(produced):
        yield path, published, produced
    elif isinstance(published, dict):
        for key in sorted(set(published) | set(produced)):
            child = f"{path}.{key}"
            if key not in published or key not in produced:
                yield child, published.get(key, "<missing>"), produced.get(key, "<missing>")
            else:
                yield from _diff(published[key], produced[key], child)
    elif isinstance(published, list):
        if len(published) != len(produced):
            yield f"{path}[]", f"{len(published)} items", f"{len(produced)} items"
        else:
            for index, (left, right) in enumerate(zip(published, produced)):
                yield from _diff(left, right, f"{path}[{index}]")
    elif published != produced:
        yield path, published, produced


def _run_policy(policy: str, seed: str, outdir: pathlib.Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "pii-leak-benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    argv = [
        sys.executable,
        "-m",
        "pii_leak_benchmark.v2_emitter",
        "--seed",
        seed,
        "--only",
        policy,
        "--out",
        str(outdir),
    ]
    print(f"\n=== running {policy}", flush=True)
    started = time.perf_counter()
    # cwd is the repo root because the emitter resolves `spec/v2.0.0/` against it.
    result = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{policy} exited {result.returncode}")
    print(f"=== {policy} finished in {time.perf_counter() - started:.1f}s", flush=True)


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def load_baselines(policies: list[str], outdir: pathlib.Path) -> dict[str, Any]:
    """Validate all destinations and snapshot immutable baselines before starting work."""
    if not policies or len(set(policies)) != len(policies):
        raise ValueError("select at least one policy, with no duplicates")
    if any(name not in SUPPORTED_POLICIES for name in policies):
        raise ValueError("supported reproduction policies: " + ", ".join(SUPPORTED_POLICIES))
    protected = (ROOT / "benchmarks" / "results").resolve()
    if outdir == protected or protected in outdir.parents or outdir in protected.parents:
        raise ValueError("output must be outside the published evidence tree and its ancestors")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["source_commit"] != EVIDENCE_COMMIT:
        raise ValueError("the evidence manifest must identify the frozen source commit")
    baseline_paths = [PUBLISHED / f"{name}.json" for name in policies]
    destinations = [outdir / f"{name}.json" for name in policies]
    destinations.append(outdir / "reproduction-summary.json")
    for destination in destinations:
        resolved = destination.resolve()
        if resolved == protected or protected in resolved.parents:
            raise ValueError("output alias points into published evidence")
        # Refuse existing artifacts, including hard links and stale successful summaries.
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"output already exists; choose a fresh directory: {destination}")
    baselines = {}
    for name, path in zip(policies, baseline_paths):
        payload = path.read_bytes().replace(b"\r\n", b"\n")
        relative = path.relative_to(ROOT).as_posix()
        if hashlib.sha256(payload).hexdigest() != manifest["sha256"][relative]:
            raise ValueError(f"frozen baseline changed: {relative}")
        baselines[name] = json.loads(payload)
    return baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / "reproduction"),
        help="directory for the regenerated reports and the summary (default: ./reproduction)",
    )
    parser.add_argument("--seed", default=PUBLISHED_SEED, help="hex seed of the published run")
    parser.add_argument(
        "--policies",
        default=",".join(POLICIES),
        help="comma-separated policy names to reproduce",
    )
    args = parser.parse_args(argv)

    outdir = pathlib.Path(args.out).resolve()
    policies = [name.strip() for name in args.policies.split(",") if name.strip()]
    try:
        baselines = load_baselines(policies, outdir)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.seed != PUBLISHED_SEED:
        parser.error("calibration must use the frozen published seed")
    outdir.mkdir(parents=True, exist_ok=True)

    for policy in policies:
        _run_policy(policy, args.seed, outdir)

    comparisons: dict[str, Any] = {}
    failures = 0
    for policy in policies:
        published_path = PUBLISHED / f"{policy}.json"
        produced_path = outdir / f"{policy}.json"
        published = baselines[policy]
        produced = json.loads(produced_path.read_text(encoding="utf-8"))
        drift = [
            {"path": path, "published": left, "produced": right}
            for path, left, right in _diff(published, produced)
        ]
        if drift:
            failures += 1
        comparisons[policy] = {
            "published_report": published_path.relative_to(ROOT).as_posix(),
            "produced_report": str(produced_path),
            "headlines": {
                label: {"published": _dig(published, path), "produced": _dig(produced, path)}
                for label, path in HEADLINES
            },
            "fields_not_compared": sorted(NONDETERMINISTIC),
            "unexpected_differences": drift,
            "reproduced": not drift,
        }

        print(f"\n=== {policy}")
        print(f"{'':22} {'published':>18}  {'your run':>18}  match")
        for label, path in HEADLINES:
            left, right = _dig(published, path), _dig(produced, path)
            match = "yes" if left == right else "NO"
            print(f"{label:22} {str(left):>18}  {str(right):>18}  {match}")
        if drift:
            print(f"\n  {len(drift)} unexpected difference(s) outside the host and timing fields:")
            for entry in drift[:20]:
                print(f"    {entry['path']}: {entry['published']!r} -> {entry['produced']!r}")
            if len(drift) > 20:
                print(f"    ... and {len(drift) - 20} more (see reproduction-summary.json)")
        else:
            print("\n  Every field matched except the host, timestamp and timing fields.")

    summary = {
        "reproduced": failures == 0,
        "seed": args.seed,
        "evidence_commit": EVIDENCE_COMMIT,
        "policies": policies,
        "command": " ".join([sys.executable, *sys.argv]),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "source_revision": _git_revision(),
        },
        "comparisons": comparisons,
    }
    summary_path = write_json_artifact(outdir / "reproduction-summary.json", summary, indent=2)

    print(f"\nSummary written to {summary_path}")
    if failures:
        print(f"RESULT: {failures} of {len(policies)} policies did NOT reproduce.")
        return 1
    print(f"RESULT: all {len(policies)} policies reproduced the published reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
