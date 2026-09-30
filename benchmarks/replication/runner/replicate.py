"""Replay a published seed sweep against a running gateway and compare it to the committed row.

This is the harness side of a replication pack (`benchmarks/replication/<gateway>/`). It
runs `pii-leak-benchmark-v2` once per published seed, rebuilds the sweep file in the same
shape as `benchmarks/v2_seed_sweep.py` writes it, and compares every number to the
published one: each seed's four rates and case counts, the summary statistics, the
request-path leak, the outcome, and the instrument digests. When the pack names the
single-seed report behind the results-wall row, that report is replayed and compared too.

Exit code 0 means every compared number matched. 1 means at least one did not, and the
differences are listed. 2 means the comparison never happened: the gateway did not answer,
or a run failed before it produced a report.

Nothing here scores anything. The rates come from the harness; this file only replays,
aggregates and compares.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

METRICS = ("fidelity_rate", "leak_single_chunk", "leak_adversarial", "delta_frag")
ROW_KEYS = METRICS + (
    "inconclusive",
    "echo_observable",
    "cases_applicable",
    "cases_attempted",
    "outcome",
    "request_path_leak",
)
# What the results-wall row reads from the single-seed report, plus the instrument.
REPORT_PATHS = (
    ("outcome",),
    ("metrics", "fidelity_rate"),
    ("metrics", "leak_rate", "single_chunk"),
    ("metrics", "leak_rate", "adversarial"),
    ("metrics", "delta_frag"),
    ("metrics", "cases_applicable"),
    ("metrics", "cases_inconclusive"),
    ("metrics", "cases_echo_observable"),
    ("metrics", "by_axis", "fragmentation", "single_chunk", "leaked"),
    ("metrics", "by_axis", "fragmentation", "adversarial", "leaked"),
    ("checks", "configured_upstream_boundary", "leaked_entity_types"),
    ("checks", "response_fidelity", "passed"),
    ("instrument",),
)


def aggregate(policy: str, rows: list[dict[str, Any]], seeds: list[str], instrument: dict[str, str]) -> dict[str, Any]:
    """The per-policy block of a sweep file, exactly as `v2_seed_sweep.py` builds it."""
    return {
        "runs": [{"seed": row["seed"], **{k: row[k] for k in ROW_KEYS}} for row in rows],
        "summary": {
            m: {
                "mean": round(statistics.fmean(r[m] for r in rows), 4),
                "min": min(r[m] for r in rows),
                "max": max(r[m] for r in rows),
                "stdev": round(statistics.stdev([r[m] for r in rows]), 4) if len(rows) > 1 else 0.0,
            }
            for m in METRICS
        },
        "seeds": seeds,
        "instrument": instrument,
    }


def compare_sweep(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Every difference between a published sweep block and a replayed one, as text."""
    differences: list[str] = []
    if expected["seeds"] != actual["seeds"]:
        differences.append(f"seeds: published {expected['seeds']}, this run {actual['seeds']}")
    for key in sorted(set(expected["instrument"]) | set(actual["instrument"])):
        if expected["instrument"].get(key) != actual["instrument"].get(key):
            differences.append(
                f"instrument.{key}: published {expected['instrument'].get(key)}, this run {actual['instrument'].get(key)}"
            )
    by_seed = {run["seed"]: run for run in actual["runs"]}
    for run in expected["runs"]:
        mine = by_seed.get(run["seed"])
        if mine is None:
            differences.append(f"seed {run['seed']}: not replayed")
            continue
        for key in ROW_KEYS:
            if run.get(key) != mine.get(key):
                differences.append(f"seed {run['seed']} {key}: published {run.get(key)}, this run {mine.get(key)}")
    for metric in METRICS:
        for stat in ("mean", "min", "max", "stdev"):
            want = expected["summary"][metric][stat]
            got = actual["summary"][metric][stat]
            if want != got:
                differences.append(f"summary {metric}.{stat}: published {want}, this run {got}")
    return differences


def _dig(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return "<absent>"
        node = node[key]
    return node


def compare_report(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """The results-wall fields of a single-seed report, published against replayed."""
    differences: list[str] = []
    if expected["corpus"]["seed"] != actual["corpus"]["seed"]:
        differences.append(f"seed: published {expected['corpus']['seed']}, this run {actual['corpus']['seed']}")
    for path in REPORT_PATHS:
        want, got = _dig(expected, path), _dig(actual, path)
        if want != got:
            differences.append(f"{'.'.join(path)}: published {want}, this run {got}")
    return differences


def one_line(policy: str, block: dict[str, Any], differences: list[str]) -> str:
    s = block["summary"]

    def cell(metric: str) -> str:
        st = s[metric]
        if st["min"] == st["max"]:
            return f"{st['mean']:.4g}"
        return f"{st['mean']:.4g} [{st['min']:.4g}-{st['max']:.4g}]"

    outcomes = sorted({r["outcome"] for r in block["runs"]})
    leaks = sorted({",".join(r["request_path_leak"]) or "none" for r in block["runs"]})
    verdict = "MATCHES the published row" if not differences else f"DIFFERS from the published row ({len(differences)})"
    return (
        f"{policy}: {len(block['runs'])} seeds, fidelity {cell('fidelity_rate')}, "
        f"leak(1chunk) {cell('leak_single_chunk')}, leak(adv) {cell('leak_adversarial')}, "
        f"DeltaFrag {cell('delta_frag')}, request-path leak {'/'.join(leaks)}, outcome {'/'.join(outcomes)}, "
        f"inspector {block['instrument'].get('inspector_sha256')}: {verdict}"
    )


def wait_for_gateway(url: str, seconds: float) -> bool:
    parts = urlsplit(url)
    host, port = parts.hostname or "127.0.0.1", parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


def run_seed(args: argparse.Namespace, seed: str, out: pathlib.Path) -> dict[str, Any] | None:
    """One `pii-leak-benchmark-v2` run; returns its `--json-out` row, or None if it produced none."""
    reports = out / "reports" / seed
    summary = out / "summaries" / f"{seed}.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        args.command,
        "--validate",
        "--only",
        args.policy,
        "--gateway-url",
        args.gateway_url,
        "--upstream-port",
        str(args.upstream_port),
        "--model",
        args.model,
        "--seed",
        seed,
        "--out",
        str(reports),
        "--json-out",
        str(summary),
    ]
    print(f"== seed {seed} ==", flush=True)
    subprocess.run(argv, check=False)
    if not summary.exists():
        return None
    (row,) = json.loads(summary.read_text(encoding="utf-8"))["rows"]
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", required=True, help="the row's policy name, e.g. litellm-presidio")
    parser.add_argument("--gateway-url", required=True, help="the gateway's chat-completions URL")
    parser.add_argument("--model", default="capture")
    parser.add_argument("--upstream-port", type=int, default=8799)
    parser.add_argument("--expected", required=True, help="the published seed-sweep JSON for this row")
    parser.add_argument("--expected-report", default=None, help="the published single-seed report, if any")
    parser.add_argument("--out", required=True, help="directory for reports, the rebuilt sweep and the comparison")
    parser.add_argument("--command", default="pii-leak-benchmark-v2", help="the harness command to run")
    parser.add_argument("--wait-seconds", type=float, default=300.0, help="how long to wait for the gateway port")
    args = parser.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    expected_all = json.loads(pathlib.Path(args.expected).read_text(encoding="utf-8"))
    if args.policy not in expected_all:
        print(f"{args.expected} has no block for {args.policy!r}; it has {sorted(expected_all)}", file=sys.stderr)
        return 2
    expected = expected_all[args.policy]

    if not wait_for_gateway(args.gateway_url, args.wait_seconds):
        print(f"gateway at {args.gateway_url} never opened its port; nothing was measured", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    for seed in expected["seeds"]:
        row = run_seed(args, seed, out)
        if row is None:
            print(f"seed {seed}: the harness wrote no summary; nothing to compare", file=sys.stderr)
            return 2
        if row["inconclusive"] >= row["cases_attempted"]:
            print(f"seed {seed}: every case inconclusive, the gateway answered nothing", file=sys.stderr)
            return 2
        rows.append(row)

    first = json.loads((out / "summaries" / f"{expected['seeds'][0]}.json").read_text(encoding="utf-8"))
    block = aggregate(args.policy, rows, list(expected["seeds"]), first["instrument"])
    differences = compare_sweep(expected, block)

    report_differences: list[str] = []
    wall_seed = None
    if args.expected_report:
        expected_report = json.loads(pathlib.Path(args.expected_report).read_text(encoding="utf-8"))
        wall_seed = expected_report["corpus"]["seed"]
        if run_seed(args, wall_seed, out) is None:
            print(f"seed {wall_seed}: the harness wrote no summary; nothing to compare", file=sys.stderr)
            return 2
        actual_report = json.loads(
            (out / "reports" / wall_seed / f"{args.policy}.json").read_text(encoding="utf-8")
        )
        report_differences = compare_report(expected_report, actual_report)

    _write(out / "seed-sweep.json", {args.policy: block})
    _write(
        out / "comparison.json",
        {
            "policy": args.policy,
            "expected": os.path.basename(args.expected),
            "sweep_differences": differences,
            "wall_seed": wall_seed,
            "wall_report_differences": report_differences,
        },
    )
    line = one_line(args.policy, block, differences + report_differences)
    if wall_seed:
        line += f"; wall seed {wall_seed} {'matches' if not report_differences else 'differs'}"
    (out / "summary.txt").write_text(line + "\n", encoding="utf-8")
    print()
    for item in differences + report_differences:
        print("  !", item)
    print(line, flush=True)
    return 0 if not (differences or report_differences) else 1


def _write(path: pathlib.Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=1, sort_keys=False, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
