"""Drive the FIDE profile across seeds and oracles, into a staging tree.

The FIDE corpus has two needle classes and the seed does NOT move them equally: the four
PII needles are drawn per seed, the four secret needles are fixed literals. So a seed sweep
of this corpus is a sweep of HALF of it, and the sweep file says so rather than presenting
twelve identical secret rows as twelve observations.

Stages:

    midpoint      6 controls x 12 seeds, midpoint oracle          ~80 min
    worstcase     6 controls x 1 seed, union of both families     ~40 min
    twopart       2 controls x 12 seeds, exhaustive-2-part        ~25 min
    shield        the author's gateway, which is the only measured shipping product
                  with a DOCUMENTED secret-scanning claim         ~5 min

Usage:
    python benchmarks/fide_sweep.py midpoint worstcase
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGING = ROOT / "benchmarks" / "results" / "staging-fide"
PUBLISHED = ROOT / "benchmarks" / "results" / "fide-v2.1"
SEEDS = [f"{i:016x}" for i in range(1, 13)]
PUBLISHED_SEED = "a1b2c3d4e5f60001"

# `summarise` runs in this process rather than in `_emit`'s child environment.  Put the
# repository package first here as well, or a standalone summarise invocation can import
# an older installed benchmark and silently classify a newer registry with stale code.
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))
from pii_leak_benchmark.needle_registry import class_of  # noqa: E402

CONTROLS = [
    "fide-passthrough",
    "fide-redact-all",
    "fide-chunk-local",
    "fide-whitespace-retention",
    "fide-length-bounded-retention",
    "fide-retention-plus-decoding",
]
# The two rows the class comparison turns on: the modelled defect and the corrected
# retention control.
CONTRAST = ["fide-chunk-local", "fide-length-bounded-retention"]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "pii-leak-benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _emit(policies: list[str], out: pathlib.Path, seed: str, oracle: str,
          gateway: tuple[str, str, int] | None = None,
          extra_env: dict[str, str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable, "-m", "pii_leak_benchmark.fide_emitter", "--validate",
        "--only", ",".join(policies), "--seed", seed, "--oracle", oracle,
        "--out", str(out),
    ]
    if gateway:
        url, model, port = gateway
        argv += ["--gateway-url", url, "--model", model, "--upstream-port", str(port)]
    label = f"{','.join(policies)} seed={seed} oracle={oracle} -> {out}"
    print(f"\n=== {label}", flush=True)
    started = time.perf_counter()
    env = _env()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    print(f"--- exit {result.returncode} in {time.perf_counter() - started:.0f}s", flush=True)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed")


# THE SEED SWEEP CARRIES THREE CONTROLS, NOT SIX, AND THE REASON IS THE CORPUS.
#
# The seed moves the four PII needles and nothing else: the four secret needles are fixed
# literals (`needle_registry.why_secrets_are_not_seeded`). So a seed sweep of this corpus is
# a sweep of half of it, and the half it does not move is measured identically at every
# seed. Sweeping `fide-passthrough` and `fide-redact-all` across twelve seeds would spend
# 25 minutes confirming that a policy which forwards everything forwards everything.
#
# The three kept controls are the ones where the seed can change the answer: the modelled
# defect, the retention rule whose precondition the corpus deliberately violates, and the
# corrected retention rule. All six are measured at the published seed.
SWEPT_CONTROLS = [
    "fide-chunk-local",
    "fide-whitespace-retention",
    "fide-length-bounded-retention",
]


def stage_midpoint() -> None:
    _emit(CONTROLS, STAGING / "midpoint" / PUBLISHED_SEED, PUBLISHED_SEED, "midpoint")
    for seed in SEEDS:
        _emit(SWEPT_CONTROLS, STAGING / "midpoint" / seed, seed, "midpoint")


def stage_twopart() -> None:
    for seed in SEEDS:
        _emit(CONTRAST, STAGING / "exhaustive-2-part" / seed, seed, "exhaustive-2-part")


# The union oracle is ~22,000 requests per policy on this corpus. `fide-passthrough` and
# `fide-redact-all` are degenerate under it BY CONSTRUCTION -- passthrough leaks every case
# in both arms whatever the partition, and redact-all's adversarial arm is chunk-local's
# with the rehydration removed -- so enumerating them buys 15 minutes of confirmation that
# a constant is constant. The four kept rows are the ones where the union can differ from
# the two-part family. The omission is a RESOURCE CAP and is published as one.
WORST_CASE_CONTROLS = [
    "fide-chunk-local",
    "fide-whitespace-retention",
    "fide-length-bounded-retention",
    "fide-retention-plus-decoding",
]


def stage_worstcase() -> None:
    _emit(WORST_CASE_CONTROLS, STAGING / "worst-case" / PUBLISHED_SEED, PUBLISHED_SEED,
          "union-worst-case")


def stage_shield() -> None:
    """LLM-Shield-Proxy 1.6.0 with response scanning on, port 8813.

    THE ONLY MEASURED SHIPPING PRODUCT ELIGIBLE FOR THIS FAMILY, and the eligibility is
    itself worth stating: its published documentation names `AWS_API_KEY`, `GITHUB_PAT` and
    `SSH_PRIVATE_KEY` as supported types. It documents NO Slack pattern, so `SLACKBOT` is
    `not-applicable` for this target and its cases must be read as untested rather than as
    misses. The author maintains this gateway; see the conflict-of-interest note in the
    claim ledger.
    """
    stage_shield_midpoint()
    stage_shield_exhaustive()


def _shield_oracle(name: str, oracle: str) -> None:
    _emit(
        # In external-gateway mode this is metadata only.  It must not reuse a
        # reference-control name: doing so attributes the product row to the study's
        # detector set and makes the aggregate merge two different targets.
        ["llm-shield-proxy-1.6.0-response-on"],
        STAGING / "shield-1.6.0-response-on" / name, PUBLISHED_SEED, oracle,
        gateway=("http://127.0.0.1:8813/v1/chat/completions", "capture", 8799),
        extra_env={
            "V2_GATEWAY_TOKEN": "sk-shield-v2-profile",
            "V2_REQUEST_PATH_REDACTION": "configured",
        },
    )


def stage_shield_midpoint() -> None:
    """Recover only the product midpoint row without repeating its expensive companion."""
    _shield_oracle("midpoint", "midpoint")


def stage_shield_exhaustive() -> None:
    """Recover only the product two-part row without repeating the valid midpoint row."""
    _shield_oracle("exhaustive-2-part", "exhaustive-2-part")


def claim_scoped_metrics(report: dict) -> dict:
    """Recompute rates over only the needles the target documents as enabled.

    The schema report always carries the full fixed corpus so rows retain one corpus
    digest and remain structurally comparable.  A product claim can be narrower: Shield
    1.6.0 documents AWS, GitHub and private-key patterns but no Slack-token pattern.
    Publishing its corpus-wide secret rate as a product rate would therefore count an
    explicitly not-applicable case as a detector miss.  The aggregate keeps both views
    and labels this denominator explicitly.
    """
    enabled = set(report["entity_scope"]["enabled"])
    entity_arms = report["metrics"]["by_axis_arm"]["entity"]

    def aggregate(entity_ids: set[str]) -> dict:
        out: dict[str, dict | float | None] = {}
        for arm_name in ("single_chunk", "adversarial"):
            applicable = sum(entity_arms[e][arm_name]["applicable"] for e in entity_ids)
            leaked = sum(entity_arms[e][arm_name]["leaked"] for e in entity_ids)
            out[arm_name] = {
                "applicable": applicable,
                "leaked": leaked,
                "leak_rate": round(leaked / applicable, 4) if applicable else None,
            }
        single = out["single_chunk"]["leak_rate"]
        adversarial = out["adversarial"]["leak_rate"]
        out["delta_frag"] = (
            round(adversarial - single, 4)
            if single is not None and adversarial is not None
            else None
        )
        return out

    by_class = {
        cls: aggregate({entity for entity in enabled if class_of(entity) == cls})
        for cls in sorted({class_of(entity) for entity in enabled})
    }
    return {
        "definition": (
            "Rates recomputed from metrics.by_axis_arm.entity over only "
            "entity_scope.enabled; not_enabled needles are excluded rather than scored "
            "as misses. The schema report's corpus-wide rates remain unchanged."
        ),
        "enabled_entities": sorted(enabled),
        "not_enabled_entities": sorted(set(entity_arms) - enabled),
        "overall": aggregate(enabled),
        "by_class": by_class,
    }


def summarise() -> int:
    """Per-class aggregates over the staged tree, recomputed from the report JSON."""
    out: dict[str, dict] = {}
    for path in sorted(STAGING.rglob("*.json")):
        if path.name.endswith(".summary.json") or path.name == "fide-sweep.json":
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        oracle = report["metrics"]["partition_oracle"]["oracle"]
        policy = path.stem
        key = f"{policy}::{oracle}"
        arms = report["metrics"]["by_axis_arm"]["needle_class"]
        row = {
            "source": path.relative_to(STAGING).as_posix(),
            "seed": report["corpus"]["seed"],
            "delta_frag": report["metrics"]["delta_frag"],
            "fidelity_rate": report["metrics"]["fidelity_rate"],
            "cases_applicable": report["metrics"]["cases_applicable"],
            "inconclusive": report["metrics"]["cases_inconclusive"],
            "detector_blind_entities": report["metrics"]["detector_blind_entities"],
            "by_class": {
                cls: {
                    "leak_single": arm["single_chunk"]["leak_rate"],
                    "leak_adversarial": arm["adversarial"]["leak_rate"],
                    "delta_frag": arm["delta_frag"],
                    "pairs": arm["paired_cases"],
                }
                for cls, arm in arms.items()
            },
            "claim_scoped": claim_scoped_metrics(report),
        }
        out.setdefault(key, {"runs": [], "instrument": report["instrument"],
                             "corpus_sha256": report["corpus"]["sha256"],
                             "needle_registry_sha256":
                                 report["corpus"]["needle_registry_sha256"]})
        out[key]["runs"].append(row)

    for key, block in out.items():
        runs = block["runs"]
        block["seeds"] = sorted({r["seed"] for r in runs})
        systematic = [r for r in runs if r["seed"] in set(SEEDS)]
        summary_runs = systematic or runs
        block["summary_seeds"] = sorted({r["seed"] for r in summary_runs})
        block["summary_frame"] = (
            "systematically enumerated seeds 0000000000000001 through "
            "000000000000000c; the hand-picked published seed is excluded"
            if systematic
            else "published-seed point observation; no seed distribution is estimated"
        )
        block["published_seed_observation"] = [
            r for r in runs if r["seed"] == PUBLISHED_SEED
        ]
        classes = sorted({c for r in runs for c in r["by_class"]})
        block["summary"] = {
            "delta_frag": _stats([r["delta_frag"] for r in summary_runs]),
            "fidelity_rate": _stats([r["fidelity_rate"] for r in summary_runs]),
            "by_class": {
                cls: {
                    metric: _stats([r["by_class"][cls][metric] for r in summary_runs
                                    if cls in r["by_class"]])
                    for metric in ("leak_single", "leak_adversarial", "delta_frag")
                }
                for cls in classes
            },
        }
        # THE SEED IS NOT A SOURCE OF VARIATION FOR HALF THIS CORPUS, and a sweep file
        # that did not say so would present twelve copies of one number as twelve
        # observations.
        block["seed_variation_note"] = (
            "The four PII needles are drawn per seed; the four secret needles are fixed "
            "literals (see needle_registry.why_secrets_are_not_seeded). Secret-class rates "
            "therefore have zero seed variance BY CONSTRUCTION and no seed-level interval "
            "is identified for them."
        )
    target = STAGING / "fide-sweep.json"
    target.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {target}")
    for key, block in sorted(out.items()):
        s = block["summary"]
        print(
            f"\n{key}  ({len(block['runs'])} runs, "
            f"summary seeds {len(block['summary_seeds'])})"
        )
        print(f"  DeltaFrag overall {s['delta_frag']['mean']} "
              f"[{s['delta_frag']['min']}-{s['delta_frag']['max']}]")
        for cls, stats in s["by_class"].items():
            print(f"  {cls:8} single {stats['leak_single']['mean']:<8} "
                  f"adv {stats['leak_adversarial']['mean']:<8} "
                  f"DeltaFrag {stats['delta_frag']['mean']} "
                  f"[{stats['delta_frag']['min']}-{stats['delta_frag']['max']}]")
    return 0


def publishable_fide_files(root: pathlib.Path = STAGING) -> list[pathlib.Path]:
    """Raw reports plus the one audited aggregate; never emitter sidecars."""
    return sorted(
        path
        for path in root.rglob("*.json")
        if not path.name.endswith(".summary.json")
    )


def stage_promote() -> int:
    """Mirror the staged experiment layout without publishing derived sidecars."""
    import shutil

    paths = publishable_fide_files()
    if not paths:
        raise SystemExit(f"nothing staged at {STAGING}")
    for path in paths:
        target = PUBLISHED / path.relative_to(STAGING)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    print(f"promoted {len(paths)} FIDE files into {PUBLISHED}")
    return 0


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "mean": round(statistics.fmean(values), 4),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "n": len(values),
    }


STAGES = {
    "midpoint": stage_midpoint,
    "twopart": stage_twopart,
    "worstcase": stage_worstcase,
    "shield": stage_shield,
    "shield-midpoint": stage_shield_midpoint,
    "shield-exhaustive": stage_shield_exhaustive,
    "summarise": summarise,
    "promote": stage_promote,
}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for name in argv:
        if name not in STAGES:
            raise SystemExit(f"unknown stage {name!r}; known: {sorted(STAGES)}")
        STAGES[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
