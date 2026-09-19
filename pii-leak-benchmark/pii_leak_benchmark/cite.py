"""``pii-leak-benchmark cite`` -- turn a report into a citable block.

A maintainer who runs the harness and says "it passed" has produced a claim nobody can
check. The same maintainer pasting this block has produced a reference: it names the
harness revision, the inspector and corpus digests, and the exact configuration the
numbers came from, so a reader can rerun the same instrument against the same corpus
and compare.

Standard library only. Nothing here may import ``llm_shield_proxy``: the benchmark is
the neutral measurer and the proxy is one of the things it measures.

WHAT THIS IS NOT. Every value is copied from a report the caller supplies, and reports
are produced by whoever ran the harness. This block is therefore SELF-REPORTED and
forgeable by its author, exactly as ``provenance.build_attestation`` says of its own
fields. It makes a result *checkable* -- a reader can rerun the named instrument -- not
*attested*. Do not add language here implying verification that no verifier performed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional, Sequence

_MISSING = "unrecorded"


def _dig(report: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _rate(value: Any) -> str:
    """Render a rate the way the reports and the spec do: 1.00, not 1.

    A bare ``1`` next to ``0.125`` reads as a count rather than a rate, and these
    blocks get pasted straight into issues where nobody has the schema to hand.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _MISSING
    text = f"{value:.4f}".rstrip("0")
    return text + "00" if text.endswith(".") else text


def _flatten(value: str) -> str:
    """Collapse line breaks to spaces. A multi-line value renders as two fields."""
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _cell(value: str) -> str:
    """Make a report-supplied value safe to sit inside a Markdown table cell.

    Every value here is free-form text from a report the caller supplied, and the
    rendered block is pasted straight into issues and READMEs. A `|` splits the row into
    extra cells, a newline ends it early and starts a bogus one, and a backtick closes
    the inline-code span so the rest of the value escapes into prose. Any of those turns
    a block meant to be CHECKABLE into one that is merely misleading, which is the exact
    property this command exists to provide.

    Escaped rather than stripped: a value is evidence, and silently deleting characters
    from it would make two different reports render identically.
    """
    escaped = value.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
    # Newlines cannot be escaped into a table cell at all; Markdown ends the row there.
    return _flatten(escaped)


def _short(digest: Any, keep: int = 16) -> str:
    if not isinstance(digest, str) or not digest:
        return _MISSING
    return digest if len(digest) <= keep else digest[:keep]


def build_citation(report: dict[str, Any], *, style: str = "markdown") -> str:
    """Render a citation block for one conformance report."""
    metrics = _dig(report, "metrics", default={})
    # Guard the TYPE, not just falsiness. `or {}` still lets a truthy non-object
    # through -- `"metrics": "unavailable"` in a hand-edited report reached `.get`
    # and raised, which breaks the documented contract that missing fields degrade
    # to `unrecorded` rather than traceback at a caller who named any JSON file.
    if not isinstance(metrics, dict):
        metrics = {}
    leak = metrics.get("leak_rate") if isinstance(metrics.get("leak_rate"), dict) else {}

    try:  # pragma: no cover - trivial, and absent only in a broken install
        from pii_leak_benchmark import __version__ as package_version
    except Exception:
        package_version = _MISSING

    rows: list[tuple[str, str]] = [
        ("Benchmark package", str(package_version)),
        ("Harness revision", str(_dig(report, "harness_revision", default=_MISSING))),
        ("Schema", str(_dig(report, "schema", default=_MISSING))),
    ]

    # The research profiles (v2, FIDE) carry scorer and corpus digests. Operator runs
    # do not, and padding their block with four "unrecorded" rows makes a usable
    # result look like a broken one, so these appear only when the report has them.
    for label, path in (
        ("Inspector digest", ("instrument", "inspector_sha256")),
        ("Corpus digest", ("corpus", "sha256")),
    ):
        value = _dig(report, *path)
        if value:
            rows.append((label, _short(value)))
    for label, path in (
        ("Corpus cases", ("corpus", "case_count")),
        ("Seed", ("corpus", "seed")),
    ):
        value = _dig(report, *path)
        if value is not None:
            rows.append((label, str(value)))

    rows.append(("Target", str(_dig(report, "implementation", "name", default=_MISSING))))
    rows.append(("Target version", str(_dig(report, "implementation", "version", default=_MISSING))))
    rows.append(("Outcome", str(report.get("outcome", _MISSING))))
    if isinstance(report.get("passed"), bool):
        rows.append(("Checks passed", "yes" if report["passed"] else "no"))

    if leak:
        rows.append(("Leak (single-chunk)", _rate(leak.get("single_chunk"))))
        rows.append(("Leak (adversarial)", _rate(leak.get("adversarial"))))
    if "delta_frag" in metrics:
        rows.append(("DeltaFrag", _rate(metrics.get("delta_frag"))))
    if "fidelity_rate" in metrics:
        rows.append(("Fidelity", _rate(metrics.get("fidelity_rate"))))
    for label, key in (
        ("Applicable cases", "cases_applicable"),
        ("Inconclusive cases", "cases_inconclusive"),
    ):
        if key in metrics:
            rows.append((label, str(metrics[key])))

    rows.append(("Platform", str(_dig(report, "environment", "platform", default=_MISSING))))
    rows.append(("Python", str(_dig(report, "environment", "python", default=_MISSING))))
    rows.append(("Generated", str(report.get("generated_at", _MISSING))))

    attestation = report.get("attestation") or report.get("provenance")
    if isinstance(attestation, dict):
        for label, key in (("Repository", "repository"), ("Run URL", "run_url")):
            if attestation.get(key):
                rows.append((label, str(attestation[key])))

    caveat = (
        "Self-reported: these values are copied from a report produced by whoever ran "
        "the harness. They make the result rerunnable, not independently attested. "
        "'Benchmark package' is the version that rendered this block, which is the "
        "version that produced the report unless it was rendered later."
    )

    if style == "text":
        # Markdown escaping would be noise here, but a newline still breaks the block:
        # it silently turns one field into what looks like two, so it is flattened.
        flat = [(_flatten(label), _flatten(value)) for label, value in rows]
        width = max(len(label) for label, _ in flat)
        body = "\n".join(f"{label.ljust(width)}  {value}" for label, value in flat)
        return f"pii-leak-benchmark result\n{body}\n\n{caveat}\n"

    lines = [
        "<!-- pii-leak-benchmark citation -->",
        "| Field | Value |",
        "|---|---|",
    ]
    lines += [f"| {_cell(label)} | `{_cell(value)}` |" for label, value in rows]
    lines += ["", f"_{caveat}_"]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pii-leak-benchmark cite",
        description=(
            "Print a citable block for a conformance report, so a result posted in an "
            "issue or README names the instrument that produced it."
        ),
    )
    parser.add_argument(
        "report",
        nargs="?",
        default="./PII_LEAK_BENCHMARK_LATEST.json",
        help="Report JSON to cite (default: ./PII_LEAK_BENCHMARK_LATEST.json).",
    )
    parser.add_argument(
        "--style",
        choices=("markdown", "text"),
        default="markdown",
        help="markdown for issues and READMEs; text for terminals and logs.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except OSError as exc:
        print(f"Cannot read report: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"Report is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(report, dict):
        print("Report is not a JSON object.", file=sys.stderr)
        return 2

    sys.stdout.write(build_citation(report, style=args.style))
    return 0


if __name__ == "__main__":
    sys.exit(main())
