"""Generate the public results page from the published JSON reports.

The results page used to be typed by hand, and it drifted: it showed a v1.0.0 table with
LLM-Shield-Proxy passing while the v2 evidence tree said no shipping configuration passes
anywhere in 95 reports. Both statements were about different profiles and neither was a
lie, which is exactly how that page misled -- a reader had no way to see the second result
at all, because it was never published anywhere but an internal log.

So the page is no longer written. It is derived from the committed reports, and CI fails
if the committed page and the committed reports disagree. A number on the site cannot
outlive the artefact it came from.

WHAT IS DERIVED AND WHAT IS NOT.

Every rate, denominator, count and outcome comes from the JSON. The prose around the
tables is hand-written and lives outside the generated block; this script never touches
it. The one piece of editorial metadata here is `GROUP_ORDER`, which only orders the two
groups the reports already declare through `implementation.name`.

Usage:
    python benchmarks/generate_results_page.py            # rewrite the page
    python benchmarks/generate_results_page.py --check    # exit 1 if it would change
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
V2 = ROOT / "benchmarks" / "results" / "v2-response-split"
FIDE = ROOT / "benchmarks" / "results" / "fide-v2.1"
V1_REPORT = ROOT / "benchmarks" / "results" / "conformance-v1.0.0-1.6.0-windows.json"
PAGE = ROOT / "website" / "docs" / "conformance" / "results.md"

BEGIN = "<!-- BEGIN GENERATED: benchmarks/generate_results_page.py -->"
END = "<!-- END GENERATED -->"

# `implementation.name` carries the prefix; this only fixes the display order and label.
GROUP_ORDER = [
    ("reference-policy:", "Reference policies (study controls, not products)"),
    ("external-gateway:", "Measured gateways and libraries"),
]


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        text = f"{value:.{places}f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _single_runs() -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for path in sorted(V2.glob("*.json")):
        if path.name.startswith("seed-sweep"):
            continue
        rows.append((path.stem, _load(path)))
    return rows


def _sweeps() -> list[tuple[str, str, dict[str, Any]]]:
    rows = []
    for path in sorted(V2.glob("seed-sweep*.json")):
        payload = _load(path)
        for policy, block in sorted(payload.items()):
            if isinstance(block, dict) and "summary" in block:
                rows.append((path.name, policy, block))
    return rows


def _mean_range(stat: dict[str, Any] | None) -> str:
    if not stat:
        return "n/a"
    mean = _fmt(stat.get("mean"))
    low, high = stat.get("min"), stat.get("max")
    if low is None or high is None or low == high:
        return mean
    return f"{mean} [{_fmt(low)}--{_fmt(high)}]"


def _request_path(report: dict[str, Any]) -> str:
    """What the target sent UPSTREAM, which the four response rates do not describe.

    Without this column the page is misleading in one direction. `litellm-presidio` and
    `nemo-guardrails-0.24.0` both show leak 0.00 in both response arms and still read
    `fail`, and nothing in the row said why: all four entity types reached the capture
    server on the REQUEST path.

    Reporting the observed values ALONE is misleading in the other direction, and unfairly
    so. An unmasked value from a target that never enabled request redaction is the
    configuration working as asked; an unmasked value from a target that did enable it is a
    control that failed. NeMo, Portkey and Guardrails AI are `not-configured` here and
    LiteLLM is `configured`, so a column that printed "leak" for all four would accuse
    three products of a defect they were never configured to prevent. The claim is read
    from `redaction_claim.request_path_redaction_configured` and always shown with the
    observation.
    """
    check = report.get("checks", {}).get("configured_upstream_boundary")
    if not isinstance(check, dict):
        return "not measured"
    claim = report.get("redaction_claim", {}).get("request_path_redaction_configured")
    leaked = check.get("leaked_entity_types") or []

    if claim == "not-configured":
        # Not a finding. The values were expected to pass through.
        return "not configured" + (f" ({len(leaked)} types seen)" if leaked else "")
    if claim == "configured":
        return "configured, leaked: " + ", ".join(leaked) if leaked else "configured, clean"

    # Everything else is `unknown` or a missing field, and the right label depends on who
    # the report is about. A reference policy is an in-process model that genuinely has no
    # request-path claim to make. A MEASURED PRODUCT with no recorded claim is an evidence
    # gap, and rendering it identically to the reference policies would hide that gap
    # behind a label the page defines as intentional.
    seen = f" ({len(leaked)} types seen)" if leaked else ""
    if report["implementation"]["name"].startswith("reference-policy:"):
        return "not applicable" + seen
    return "claim not recorded" + seen


def _count_reports(root: pathlib.Path) -> int:
    """Reports in the tree, excluding sweep aggregates, which are not reports."""
    return sum(
        1
        for path in root.rglob("*.json")
        if not path.name.startswith("seed-sweep") and not path.name.endswith("sweep.json")
    )


def _render() -> str:
    out: list[str] = [BEGIN, ""]
    out.append("_This section is generated from the committed JSON reports by")
    out.append("`benchmarks/generate_results_page.py`. Do not edit it by hand; edit the reports")
    out.append("or the generator. CI fails if this block and the reports disagree._")
    out.append("")

    singles = _single_runs()
    reference = _load(V2 / "chunk-local.json")
    out.append("## v2 response-split profile")
    out.append("")
    out.append(
        f"Corpus `{reference['corpus']['sha256'][:16]}...` at seed "
        f"`{reference['corpus']['seed']}`, inspector "
        f"`{reference['instrument']['inspector_sha256']}`, "
        f"{reference['metrics']['cases_applicable']} applicable cases per run "
        f"({reference['metrics']['cases_by_condition']['single_chunk']} single-chunk and "
        f"{reference['metrics']['cases_by_condition']['adversarial']} fragmented). "
        "DeltaFrag is the fragmented leak rate minus the single-chunk leak rate; zero means "
        "boundary placement changed nothing."
    )
    out.append("")

    # A report whose `implementation.name` matches no prefix would vanish from every table
    # and from the pass-rate line below, while generation and CI both stayed green. The
    # schema permits any non-empty name, so this is reachable by committing a valid report.
    ungrouped = [
        f"{n} ({r['implementation']['name']})"
        for n, r in singles
        if not any(r["implementation"]["name"].startswith(p) for p, _ in GROUP_ORDER)
    ]
    if ungrouped:
        raise SystemExit(
            "these reports match no group in GROUP_ORDER and would be dropped from the "
            "published page:\n  " + "\n  ".join(ungrouped)
        )

    for prefix, heading in GROUP_ORDER:
        group = [(n, r) for n, r in singles if r["implementation"]["name"].startswith(prefix)]
        if not group:
            continue
        out.append(f"### {heading}")
        out.append("")
        out.append(
            "| Configuration | Fidelity | Leak, single | Leak, fragmented | DeltaFrag "
            "| Request path | Inconclusive | Outcome |"
        )
        out.append("| :--- | ---: | ---: | ---: | ---: | :--- | ---: | :--- |")
        for name, report in group:
            metrics = report["metrics"]
            out.append(
                f"| `{name}` | {_fmt(metrics['fidelity_rate'])} "
                f"| {_fmt(metrics['leak_rate']['single_chunk'])} "
                f"| {_fmt(metrics['leak_rate']['adversarial'])} "
                f"| {_fmt(metrics['delta_frag'])} "
                f"| {_request_path(report)} "
                f"| {metrics['cases_inconclusive']}/{metrics['cases_scored']} "
                f"| `{report['outcome']}` |"
            )
        out.append("")

    passing = [n for n, r in singles if r["outcome"] == "pass"]
    external_passing = [
        n
        for n, r in singles
        if r["outcome"] == "pass" and r["implementation"]["name"].startswith("external-gateway:")
    ]
    out.append(
        f"**{len(external_passing)} of "
        f"{len([1 for _, r in singles if r['implementation']['name'].startswith('external-gateway:')])} "
        f"measured gateway and library configurations pass.** "
        + (
            f"The only passing rows are reference policies: {', '.join(f'`{p}`' for p in passing)}."
            if passing and not external_passing
            else ""
        )
    )
    out.append("")

    sweeps = _sweeps()
    if sweeps:
        out.append("### Seed sweeps")
        out.append("")
        out.append("Mean across seeds, with minimum--maximum where the value varies.")
        out.append("")
        out.append("| Configuration | Seeds | Fidelity | Leak, single | Leak, fragmented | DeltaFrag |")
        out.append("| :--- | ---: | ---: | ---: | ---: | ---: |")
        for _, policy, block in sweeps:
            summary = block["summary"]
            out.append(
                f"| `{policy}` | {len(block.get('seeds', []))} "
                f"| {_mean_range(summary.get('fidelity_rate'))} "
                f"| {_mean_range(summary.get('leak_single_chunk'))} "
                f"| {_mean_range(summary.get('leak_adversarial'))} "
                f"| {_mean_range(summary.get('delta_frag'))} |"
            )
        out.append("")

    out.append("### Evidence inventory")
    out.append("")
    out.append(f"- v2 response-split reports: **{_count_reports(V2)}**")
    if FIDE.exists():
        out.append(f"- FIDE v2.1 reports (separate 64-case corpus): **{_count_reports(FIDE)}**")
    out.append(f"- Seed-sweep aggregates: **{len(list(V2.glob('seed-sweep*.json')))}**")
    out.append("")

    if V1_REPORT.exists():
        v1 = _load(V1_REPORT)
        out.append("## v1.0.0 local in-process profile")
        out.append("")
        out.append(
            f"`{V1_REPORT.relative_to(ROOT).as_posix()}`, schema `{v1.get('schema', 'n/a')}`, "
            f"source revision `{v1.get('source_revision', 'unknown')}`."
        )
        out.append("")
        out.append("| Check | Result |")
        out.append("| :--- | :--- |")
        # `checks` is a MAPPING of check name to its detail block, not a list of records.
        # Read as a list it yields bare strings, every `passed` lookup returns None, and
        # the page prints six FAILs for a report whose own top-level `passed` is true.
        checks = v1.get("checks", {})
        if not isinstance(checks, dict):
            raise SystemExit(f"expected `checks` to be a mapping, got {type(checks).__name__}")
        for name, detail in sorted(checks.items()):
            passed = detail.get("passed") if isinstance(detail, dict) else None
            if passed is None:
                raise SystemExit(f"v1 check {name!r} has no `passed` field")
            out.append(f"| {name} | {'pass' if passed else 'FAIL'} |")
        out.append("")
        out.append(f"Report-level `passed`: **{v1.get('passed')}**")
        out.append("")
        micro = v1.get("microbenchmarks", {})
        rows = [(k, v) for k, v in micro.items() if isinstance(v, dict) and "p50" in v]
        if rows:
            out.append("| In-process operation | p50 | p95 | p99 |")
            out.append("| :--- | ---: | ---: | ---: |")
            for name, stat in rows:
                out.append(
                    f"| {name} | {stat['p50'] / 1000:.1f} us "
                    f"| {stat['p95'] / 1000:.1f} us | {stat['p99'] / 1000:.1f} us |"
                )
            out.append("")
            out.append(f"Scope: {micro.get('scope', 'not stated')}")
            out.append("")

    out.append(END)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the page would change")
    args = parser.parse_args(argv)

    text = PAGE.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print(f"{PAGE} is missing the generated-block markers", file=sys.stderr)
        return 2
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    updated = head + _render() + tail

    if updated == text:
        print("results page is up to date")
        return 0
    if args.check:
        print(
            "results page is STALE: the committed page disagrees with the committed reports.\n"
            "Run: python benchmarks/generate_results_page.py",
            file=sys.stderr,
        )
        return 1
    # LF and a trailing newline, so the page does not change shape on Windows.
    with PAGE.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    print(f"rewrote {PAGE.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
