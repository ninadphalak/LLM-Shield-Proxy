"""Recompute every table candidate from JSON fields or source. Never from prose.

WHY THIS EXISTS, and it is a repeat offence.

The round-5 companion audited the manuscript's caption for the exhaustive split count
against `exhaustive-splits/README.md`, which was itself derived from the emitter's
`method_limits` string. All three agreed, all three were wrong, and `252` reached a
submission draft. An audit that resolves a number to another document has verified
consistency, not correctness.

THE RULE THIS FILE IMPLEMENTS: every number it emits is either

  (a) read from a named JSON field at a named path in a named artefact, or
  (b) RECOMPUTED here from such fields by arithmetic printed beside the result, or
  (c) read from source code at a named symbol.

Nothing is read from a README, a manuscript, a companion document, or this file's own
previous output. Each row carries the exact command that reproduces it.

Usage:
    python benchmarks/fide_numeric_audit.py \
        --v2 benchmarks/results/v2-response-split \
        --fide benchmarks/results/fide-v2.1 \
        --uncertainty <path to fide_uncertainty.py output>.json \
        --out .llm/research/ieee-software/fide/NUMERIC-AUDIT.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _at(document: dict, path: str) -> Any:
    node: Any = document
    for key in path.split("."):
        if isinstance(node, list):
            node = node[int(key)]
        else:
            node = node[key]
    return node


class Audit:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.rows = 0
        self.failures: list[str] = []

    def head(self, text: str) -> None:
        self.lines.append(f"\n## {text}\n")

    def note(self, text: str) -> None:
        self.lines.append(text + "\n")

    def table(self, header: list[str]) -> None:
        self.lines.append("| " + " | ".join(header) + " |")
        self.lines.append("|" + "|".join(["---"] * len(header)) + "|")

    def row(self, cells: list[str]) -> None:
        self.rows += 1
        self.lines.append("| " + " | ".join(str(c) for c in cells) + " |")

    def check(self, label: str, got: Any, want: Any) -> str:
        if got == want:
            return "OK"
        self.failures.append(f"{label}: published {got!r}, recomputed {want!r}")
        return f"**MISMATCH** {got!r} vs {want!r}"


def audit_report(audit: Audit, path: pathlib.Path, root: pathlib.Path) -> None:
    """Every headline of one report, each recomputed from a sibling field where possible."""
    report = _load(path)
    rel = path.relative_to(root).as_posix()
    metrics = report["metrics"]

    # DeltaFrag, recomputed from the two leak rates it is defined as the difference of.
    recomputed = round(
        metrics["leak_rate"]["adversarial"] - metrics["leak_rate"]["single_chunk"], 4
    )
    audit.row([
        rel, "metrics.delta_frag", metrics["delta_frag"],
        "leak_rate.adversarial - leak_rate.single_chunk",
        audit.check(f"{rel} delta_frag", metrics["delta_frag"], recomputed),
    ])

    # ...and again from the paired 2x2 table, which is an INDEPENDENT route to it.
    # Reconstruct the two stored four-decimal marginal rates before subtracting them.
    # Dividing the discordant-count difference directly can round one unit differently
    # at a half-even boundary (for example, 23 / 32 = 0.71875) even though the table and
    # the published marginals are exactly consistent.
    table = metrics.get("discordance")
    if table and table["pairs_complete"] and not table["pairs_incomplete"]:
        paired_adversarial = round(
            (table["both_arms_leaked"] + table["adversarial_only"])
            / table["pairs_complete"],
            4,
        )
        paired_single = round(
            (table["both_arms_leaked"] + table["single_chunk_only"])
            / table["pairs_complete"],
            4,
        )
        paired = round(paired_adversarial - paired_single, 4)
        audit.row([
            rel, "metrics.delta_frag (paired route)", metrics["delta_frag"],
            f"({table['adversarial_only']} - {table['single_chunk_only']}) / {table['pairs_complete']}",
            audit.check(f"{rel} delta_frag paired", metrics["delta_frag"], paired),
        ])
        cells = (
            table["both_arms_leaked"] + table["adversarial_only"]
            + table["single_chunk_only"] + table["neither_arm_leaked"]
        )
        audit.row([
            rel, "discordance.pairs_complete", table["pairs_complete"],
            "both + adv_only + single_only + neither",
            audit.check(f"{rel} table sum", table["pairs_complete"], cells),
        ])

    # The denominators.
    audit.row([
        rel, "metrics.cases_applicable", metrics["cases_applicable"],
        "cases_scored - cases_inconclusive",
        audit.check(f"{rel} applicable", metrics["cases_applicable"],
                    metrics["cases_scored"] - metrics["cases_inconclusive"]),
    ])

    # The split decomposition: the erratum this whole refresh exists for.
    block = metrics.get("partition_oracle")
    if block:
        audit.row([
            rel, "partition_oracle.captured_requests_total", block["captured_requests_total"],
            f"{block['adversarial_partitions']} adversarial partitions + "
            f"{block['uncut_single_chunk_requests']} uncut",
            audit.check(f"{rel} request total", block["captured_requests_total"],
                        block["adversarial_partitions"] + block["uncut_single_chunk_requests"]),
        ])
        # Latency is recorded only for transport-complete requests.  A partition oracle
        # can expand one logical case into many requests, so the applicable logical-case
        # count is not the latency denominator.  Subtract the oracle's transport-failed
        # request count from its captured-request total instead.
        iterations = report["checks"]["client_observed_latency"]["iterations"]
        timed_requests = (
            block["captured_requests_total"]
            - block.get("cases_inconclusive_in_transport", 0)
        )
        audit.row([
            rel, "checks.client_observed_latency.iterations", iterations,
            "captured requests minus transport-inconclusive requests",
            audit.check(f"{rel} latency iterations", iterations,
                        timed_requests),
        ])
        for family, arm in block["families"].items():
            if not arm.get("enumerated"):
                continue
            audit.row([
                rel, f"partition_oracle.families.{family}.delta_frag", arm["delta_frag"],
                "leak_rate_adversarial - leak_rate_single_chunk_paired",
                audit.check(f"{rel} {family} delta", arm["delta_frag"],
                            round(arm["leak_rate_adversarial"]
                                  - arm["leak_rate_single_chunk_paired"], 4)),
            ])
        worst = block["worst_case"]
        if worst["families_in_union"]:
            floor = max(
                block["families"][f]["leak_rate_adversarial"]
                for f in worst["families_in_union"]
            )
            audit.row([
                rel, "partition_oracle.worst_case.leak_rate_adversarial",
                worst["leak_rate_adversarial"],
                f"must be >= max component ({floor})",
                "OK" if worst["leak_rate_adversarial"] >= floor
                else f"**MISMATCH** union {worst['leak_rate_adversarial']} < {floor}",
            ])

    # FidelityRate's real denominator, which the manuscript states as 128 and which is a
    # count of ECHO ITERATIONS, not of cases.
    fidelity = report["checks"]["response_fidelity"]
    if fidelity["iterations_completed"]:
        recomputed_rate = round(
            fidelity["iterations_matching"] / fidelity["iterations_completed"], 4
        )
        published = round(metrics["fidelity_rate"], 4)
        audit.row([
            rel, "metrics.fidelity_rate", published,
            f"{fidelity['iterations_matching']} / {fidelity['iterations_completed']} echo iterations",
            audit.check(f"{rel} fidelity", published, recomputed_rate),
        ])


def audit_seed_group(audit: Audit, label: str, reports: list[pathlib.Path],
                     root: pathlib.Path) -> None:
    """Mean and range over a seed group, recomputed from the per-seed JSON fields."""
    values = []
    for path in sorted(reports):
        document = _load(path)
        values.append((document["corpus"]["seed"], document["metrics"]["delta_frag"],
                       document["metrics"]["leak_rate"]["single_chunk"],
                       document["metrics"]["leak_rate"]["adversarial"]))
    deltas = [v[1] for v in values]
    audit.row([
        label, "DeltaFrag mean [min-max]",
        f"{round(statistics.fmean(deltas), 4)} [{min(deltas)}--{max(deltas)}]",
        f"fmean/min/max over {len(deltas)} per-seed metrics.delta_frag values",
        "recomputed",
    ])
    audit.row([
        label, "LeakRate(adversarial) mean [min-max]",
        f"{round(statistics.fmean([v[3] for v in values]), 4)} "
        f"[{min(v[3] for v in values)}--{max(v[3] for v in values)}]",
        f"over {len(values)} per-seed metrics.leak_rate.adversarial",
        "recomputed",
    ])
    audit.row([
        label, "LeakRate(single_chunk) mean [min-max]",
        f"{round(statistics.fmean([v[2] for v in values]), 4)} "
        f"[{min(v[2] for v in values)}--{max(v[2] for v in values)}]",
        f"over {len(values)} per-seed metrics.leak_rate.single_chunk",
        "recomputed",
    ])
    audit.note("")
    audit.note(f"Per-seed values behind `{label}`:\n")
    audit.note("| seed | leak single | leak adversarial | DeltaFrag |")
    audit.note("|---|---|---|---|")
    for seed, delta, single, adversarial in values:
        audit.note(f"| `{seed}` | {single} | {adversarial} | {delta} |")
    audit.note("")


def _aggregate_stats(values: list[float]) -> dict[str, float | int]:
    return {
        "mean": round(statistics.fmean(values), 4),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "n": len(values),
    }


def audit_fide_aggregate(audit: Audit, root: pathlib.Path) -> None:
    """Verify the derived sweep against the raw reports it names.

    `fide-sweep.json` is intentionally not a schema report, but its values are table
    candidates.  Skipping it from report validation must not exempt it from numeric
    validation.
    """
    aggregate_path = root / "fide-sweep.json"
    if not aggregate_path.is_file():
        audit.failures.append(f"{aggregate_path}: aggregate is missing")
        return

    from fide_sweep import claim_scoped_metrics

    document = _load(aggregate_path)
    audit.head("FIDE v2.1: derived sweep, recomputed from its named raw reports")
    audit.table(["group", "field", "published", "recomputed from", "verdict"])
    named_sources = sorted(
        run["source"]
        for block in document.values()
        for run in block["runs"]
    )
    raw_sources = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.name != "fide-sweep.json" and not path.name.endswith(".summary.json")
    )
    audit.row([
        "aggregate coverage",
        "named raw sources",
        len(named_sources),
        f"all {len(raw_sources)} raw report files in the FIDE tree",
        audit.check("FIDE aggregate source coverage", named_sources, raw_sources),
    ])
    for key, block in sorted(document.items()):
        runs = block["runs"]
        for run in runs:
            source = root / run["source"]
            if not source.is_file():
                audit.failures.append(f"{key}: missing named source {run['source']}")
                continue
            report = _load(source)
            recomputed_scope = claim_scoped_metrics(report)
            arms = report["metrics"]["by_axis_arm"]["needle_class"]
            expected_run = {
                "source": run["source"],
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
                "claim_scoped": recomputed_scope,
            }
            audit.row([
                f"{key}:{run['seed']}",
                "embedded run projection",
                f"DeltaFrag={run['delta_frag']}",
                f"all aggregate inputs recomputed from {run['source']}",
                audit.check(
                    f"{key}:{run['seed']} embedded run projection",
                    run,
                    expected_run,
                ),
            ])

        summary_seed_set = set(block["summary_seeds"])
        summary_runs = [r for r in runs if r["seed"] in summary_seed_set]
        expected = {
            "delta_frag": _aggregate_stats([r["delta_frag"] for r in summary_runs]),
            "fidelity_rate": _aggregate_stats([r["fidelity_rate"] for r in summary_runs]),
            "by_class": {
                cls: {
                    metric: _aggregate_stats([
                        r["by_class"][cls][metric]
                        for r in summary_runs
                        if cls in r["by_class"]
                    ])
                    for metric in ("leak_single", "leak_adversarial", "delta_frag")
                }
                for cls in sorted({name for r in summary_runs for name in r["by_class"]})
            },
        }
        audit.row([
            key,
            "summary",
            "mean/min/max/stdev/n",
            f"{len(summary_runs)} named raw report rows in summary_seeds",
            audit.check(f"{key} aggregate summary", block["summary"], expected),
        ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2", default="benchmarks/results/v2-response-split")
    parser.add_argument("--fide", default="benchmarks/results/fide-v2.1")
    parser.add_argument("--uncertainty", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    audit = Audit()
    audit.note("# FIDE numeric audit\n")
    audit.note(
        "Every number below is read from a named JSON field or recomputed from such fields "
        "by the arithmetic printed beside it. **Nothing is resolved against a README, a "
        "manuscript, or a companion document.** That failure mode is why this file exists: "
        "the round-5 audit checked a caption against a README that was derived from the "
        "same wrong emitter string, and `252` reached a submission draft with three "
        "documents agreeing.\n"
    )
    audit.note("Reproduce the whole file with:\n")
    audit.note("```bash\npython benchmarks/fide_numeric_audit.py \\\n"
               "  --v2 benchmarks/results/v2-response-split \\\n"
               "  --fide benchmarks/results/fide-v2.1 \\\n"
               "  --uncertainty <fide_uncertainty output>.json\n```\n")

    from pii_leak_benchmark import needle_registry as nr
    from pii_leak_benchmark.v2_emitter import inspector_digest, instrument_block

    audit.head("Instrument, read from source")
    audit.table(["symbol", "value", "command"])
    audit.row(["`v2_emitter.inspector_digest()`", f"`{inspector_digest()}`",
               "`python -c \"from pii_leak_benchmark.v2_emitter import inspector_digest as d; print(d())\"`"])
    for key, value in instrument_block().items():
        audit.row([f"`instrument_block()[{key!r}]`", f"`{value}`", "same command"])
    audit.row(["`needle_registry.registry_digest()`", f"`{nr.registry_digest()}`",
               "`python -c \"from pii_leak_benchmark import needle_registry as n; print(n.registry_digest())\"`"])

    for label, root_arg in (("v2 (PII baseline)", args.v2), ("FIDE v2.1", args.fide)):
        root = pathlib.Path(root_arg)
        if not root.is_dir():
            audit.head(f"{label}: directory absent ({root})")
            continue
        reports = sorted(
            p for p in root.rglob("*.json")
            if not p.name.startswith("seed-sweep")
            and p.name != "fide-sweep.json"
            and not p.name.endswith(".summary.json")
        )
        audit.head(f"{label}: {len(reports)} reports, per-report recomputation")
        audit.table(["artefact", "field", "published", "recomputed from", "verdict"])
        for path in reports:
            audit_report(audit, path, root)

        # Seed groups: any directory holding one file per seed.
        groups: dict[str, list[pathlib.Path]] = {}
        for path in reports:
            parent = path.parent
            if parent == root:
                continue
            grandparent = parent.parent.name if parent.parent != root else ""
            if grandparent:
                groups.setdefault(f"{grandparent}/{path.stem}", []).append(path)
        if groups:
            audit.head(f"{label}: seed-group aggregates, recomputed from the per-seed files")
            audit.table(["group", "statistic", "value", "recomputed from", "verdict"])
            for name, paths in sorted(groups.items()):
                if len(paths) > 1:
                    audit_seed_group(audit, name, paths, root)

        if label == "FIDE v2.1":
            audit_fide_aggregate(audit, root)

    if args.uncertainty:
        stats = _load(pathlib.Path(args.uncertainty))
        audit.head("Uncertainty: estimand, assumptions, and the tests")
        audit.note(f"**Estimand.** {stats['estimand']}\n")
        audit.note(f"**Sampling frame.** {stats['sampling_frame']}\n")
        audit.note(f"**Generalises to.** {stats['generalises_to']}\n")
        audit.note(f"**Does NOT generalise to.** {stats['does_not_generalise_to']}\n")
        audit.note(f"**Multiplicity.** {stats['multiplicity_policy']}\n")
        audit.note(f"**Analysis plan written.** {stats['analysis_plan_written']}\n")
        audit.note(
            "Reproduce with:\n\n```bash\npython benchmarks/fide_uncertainty.py "
            "--results benchmarks/results/v2-response-split\n```\n"
        )
        audit.table([
            "group", "n", "mean", "median", "range", "sign test p (median)",
            "sign-flip p (mean)", "Holm", "median interval (exact coverage)",
            "BCa mean interval",
        ])
        for key in sorted(stats["groups"]):
            group = stats["groups"][key]
            median_ci = group["median_interval"]
            mean_ci = group["mean_interval"]
            audit.row([
                f"`{key}`", group["n_seeds"], group["mean"], group["median"],
                f"[{group['min']}, {group['max']}]",
                group["sign_test"].get("p_exact", "--"),
                group["sign_flip_test"].get("p_exact", "--"),
                group.get("holm_adjusted_p_sign_flip", "--"),
                (f"{median_ci['interval']} @ {median_ci.get('achieved_coverage')}"
                 if median_ci.get("interval") else "REFUSED (degenerate)"),
                (str(mean_ci["interval"]) if mean_ci.get("interval") else "REFUSED (degenerate)"),
            ])
        audit.note("")
        audit.note(
            "A **REFUSED** cell is not a missing number. It means every seed produced the "
            "same value, so no sampling distribution is identified in these data and an "
            "interval would be an artefact of the estimator rather than a property of the "
            "target. The measurement in those rows is the invariance itself.\n"
        )

    audit.head("Result")
    audit.note(f"{audit.rows} recomputations performed. "
               f"{len(audit.failures)} mismatches.\n")
    if audit.failures:
        for failure in audit.failures:
            audit.note(f"- **{failure}**")
    else:
        audit.note("Every published number reproduces from the fields beside it.\n")

    text = "\n".join(audit.lines)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}: {audit.rows} recomputations, {len(audit.failures)} mismatches")
    else:
        print(text)
    return 1 if audit.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
