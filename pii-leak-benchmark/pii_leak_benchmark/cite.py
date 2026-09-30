"""``pii-leak-benchmark cite`` -- turn a report into a citable block.

Produces a self-reported, checkable reference (not an attested one) containing
the configuration, harness revision, and corpus digests used for the measurement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional, Sequence

from pii_leak_benchmark import report_fields as fields

_MISSING = "unrecorded"

# Runs of backticks inside a value, which decide how long its code fence must be.
_BACKTICK_RUN = re.compile(r"`+")


def _dig(report: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _rate(value: Any) -> str:
    """Format rates as decimals (e.g. 1.00) to distinguish them from counts."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _MISSING
    text = f"{value:.4f}".rstrip("0")
    return text + "00" if text.endswith(".") else text


def _flatten(value: str) -> str:
    """Collapse line breaks to spaces. A multi-line value renders as two fields."""
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _cell(value: str) -> str:
    """Escape pipes to prevent breaking out of GFM table cells."""
    return _flatten(value).replace("|", "\\|")


def _code_cell(value: str) -> str:
    """Render a value as a code span using backtick fences sized to prevent breakouts."""
    flat = _cell(value)
    longest = max((len(run) for run in _BACKTICK_RUN.findall(flat)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


def _short(digest: Any, keep: int = 16) -> str:
    if not isinstance(digest, str) or not digest:
        return _MISSING
    return digest if len(digest) <= keep else digest[:keep]


def build_citation(report: dict[str, Any], *, style: str = "markdown") -> str:
    """Render a citation block for one conformance report."""
    metrics = _dig(report, "metrics", default={})
    # Enforce type to prevent tracebacks from hand-edited reports containing strings.
    if not isinstance(metrics, dict):
        metrics = {}
    leak = metrics.get("leak_rate") if isinstance(metrics.get("leak_rate"), dict) else {}

    try:  # pragma: no cover - trivial, and absent only in a broken install
        from pii_leak_benchmark import __version__ as package_version
    except Exception:
        package_version = _MISSING

    rows: list[tuple[str, str]] = [
        ("Benchmark package", str(package_version)),
        # Resolve harness revision from either report shape.
        ("Harness revision", fields.harness_revision(report)),
        ("Schema", str(_dig(report, "schema", default=_MISSING))),
    ]

    # Only show inspector and corpus fields if present (they are absent in operator runs).
    instrument = fields.instrument_sha256(report)
    if instrument:
        rows.append(("Inspector digest", _short(instrument)))
    corpus_digest = _dig(report, "corpus", "sha256")
    if corpus_digest:
        rows.append(("Corpus digest", _short(corpus_digest)))
    for label, path in (
        ("Corpus cases", ("corpus", "case_count")),
        ("Seed", ("corpus", "seed")),
    ):
        value = _dig(report, *path)
        if value is not None:
            rows.append((label, str(value)))

    rows.append(("Target", fields.target_name(report)))
    rows.append(("Target version", fields.target_version(report)))
    model = fields.model(report)
    if model:
        # Keep model distinct from Target, as it represents the routed alias.
        rows.append(("Model", model))
    # Extract outcome from either shape ('verdict' or 'outcome').
    rows.append(("Outcome", str(report.get("verdict") or report.get("outcome") or _MISSING)))
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

    for label, key in (("Platform", "platform"), ("Python", "python")):
        value = _dig(report, "environment", key)
        if value:
            rows.append((label, str(value)))
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
        # Flatten lines to prevent newline breaks in text format.
        flat = [(_flatten(label), _flatten(value)) for label, value in rows]
        width = max(len(label) for label, _ in flat)
        body = "\n".join(f"{label.ljust(width)}  {value}" for label, value in flat)
        return f"pii-leak-benchmark result\n{body}\n\n{caveat}\n"

    lines = [
        "<!-- pii-leak-benchmark citation -->",
        "| Field | Value |",
        "|---|---|",
    ]
    lines += [f"| {_cell(label)} | {_code_cell(value)} |" for label, value in rows]
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
