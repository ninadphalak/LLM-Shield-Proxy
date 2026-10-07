"""``pii-leak-benchmark badge`` -- render a report as a Shields.io endpoint badge.

WHY THIS EXISTS. A plain GitHub Actions badge says only "the workflow exited 0". A
gateway leaking every value shows the same green tick as one leaking none, provided
the job did not crash, so the badge a maintainer puts in their README says nothing
about the measurement. This renders the RESULT instead.

HOW IT REACHES A README. Shields.io's endpoint mode draws a badge from any JSON URL::

    https://img.shields.io/endpoint?url=https://OWNER.github.io/REPO/pii-leak-badge.json

The adopter publishes this file from their own repository. Nothing is hosted here and
there is no registry to register with, so it works identically from a fork and from a
repository this project has never heard of. The badge is built from the adopter's own
run, which is also why it is not evidence to anyone else: see below.

THIS IS NOT ATTESTATION. The JSON is generated from a report produced by whoever ran
the harness, and it is served from their web host. Both halves are under the
submitter's control, exactly as ``cite.build_citation`` says of its block. A reader
who wants to check the claim follows the badge to the run and reruns the named
instrument. Do not add wording here implying a verifier checked anything.

COLOUR IS A CLAIM, so it is derived from ``outcome`` and never from ``passed`` alone.
There are seven outcomes and only one of them is green. A run that never enabled
redaction, or that could not attribute what it saw, is not a pass with a caveat: it is
not a verdict at all, and a green badge on one would be the single most misleading
thing this package could emit.

Standard library only. Nothing here may import ``llm_shield_proxy``: the benchmark is
the neutral measurer and the proxy is one of the things it measures.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional, Sequence

from pii_leak_benchmark import report_fields as fields

DEFAULT_LABEL = "PII leak check"
DEFAULT_OUTPUT = "./pii-leak-badge.json"

# Shields renders any string, so these are the vocabulary a reader actually sees.
# Every one of them is a plain-language reading of an `outcome` value, and the three
# greys exist because "we did not learn anything" must not look like "it held".
#
# `critical` (red) is reserved for a measured leak. `inactive` (grey) means the run was
# not a verdict about the product. `blue` is a factual statement about the product
# rather than a finding. Nothing here is `success` except a real pass.
_OUTCOME_BADGE: dict[str, tuple[str, str]] = {
    "pass": ("contained", "brightgreen"),
    "fail": ("leaked", "critical"),
    # Measured, attributable, and nothing reached the capture -- but some other part of
    # the profile did not hold. Not a leak, and not a clean pass either.
    "no-leak-profile-not-met": ("no leak, profile not met", "yellow"),
    "inconclusive": ("inconclusive", "lightgrey"),
    "redaction-not-enabled": ("redaction not enabled", "lightgrey"),
    "claim-unstated": ("claim unstated", "lightgrey"),
    "not-applicable": ("no redaction offered", "blue"),
}

_UNKNOWN_BADGE = ("unknown outcome", "lightgrey")

# The OPERATOR run shape (`pii-leak-benchmark/operator-run/v1`) carries a `verdict`, not
# an `outcome`, and the two are not interchangeable.
#
# This mattered more than it looks. `outcome` is derived from the redaction CLAIM first,
# and the operator path deliberately records no claim -- `selfcheck` says as much when it
# tells you to use the flat command to publish a row. So every operator report derives
# `claim-unstated`, and a badge keyed on `outcome` came out grey on a run that had
# measured a real leak. In CI, which is the only place the badge is produced
# automatically, that meant the feature could never once show the thing it exists to
# show. Read the verdict where there is one.
_VERDICT_BADGE: dict[str, tuple[str, str]] = {
    "CLEAN": ("contained", "brightgreen"),
    "LEAK": ("leaked", "critical"),
    # Measured, no leak observed, but a required check did not hold. Not a pass.
    "CHECK FAILED": ("checks failed", "yellow"),
    "NOT MEASURED": ("not measured", "lightgrey"),
}


def _dig(report: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _leak_detail(report: dict[str, Any]) -> Optional[str]:
    """Say WHAT leaked, when the report is specific enough to support it.

    Two report shapes reach this, and they know different things. The research
    profiles count cases and can say "4 of 32". An operator run does not partition
    into scored cases but does record which entity types crossed the boundary, and
    "leaked: EMAIL, SSN" is more use to a maintainer than a bare rate.

    Returns None when the report supports neither, so the caller falls back to the
    bare verdict rather than inventing a denominator.
    """
    # Operator runs record a per-type state rather than a case partition.
    entities = report.get("entities")
    if isinstance(entities, dict):
        leaked = sorted(
            str(name) for name, state in entities.items() if str(state).lower() == "leak"
        )
        if leaked:
            return "leaked: " + ", ".join(leaked)

    leaked_types = _dig(report, "checks", "configured_upstream_boundary", "leaked_entity_types")
    if isinstance(leaked_types, list) and leaked_types:
        names = sorted(str(entry) for entry in leaked_types)
        return "leaked: " + ", ".join(names)

    metrics = _dig(report, "metrics", default={})
    if not isinstance(metrics, dict):
        return None
    applicable = metrics.get("cases_applicable")
    leak = metrics.get("leak_rate")
    overall = leak.get("overall") if isinstance(leak, dict) else None
    if not isinstance(applicable, int) or applicable <= 0:
        return None
    if isinstance(overall, bool) or not isinstance(overall, (int, float)):
        return None
    # Back out the case count from the rate. The rate is the published number and the
    # count is the readable one; rounding is safe because the rate is a quotient of
    # integers over this same denominator.
    leaked_cases = round(overall * applicable)
    if leaked_cases <= 0:
        return None
    return f"leaked {leaked_cases} of {applicable}"


def _provenance(report: dict[str, Any], verdict: Any) -> dict[str, str]:
    """Say which run produced this badge, in whichever shape the report uses.

    The field lookups live in `report_fields` because `cite` needs the same ones, and
    teaching only one of the two is how a CI report came to be cited with `unrecorded`
    in every provenance row while the file beside it held the answers.
    """
    block = {
        "result": str(verdict or report.get("outcome") or fields.MISSING),
        "harness_revision": fields.harness_revision(report),
        "target": fields.target_name(report),
        "target_version": fields.target_version(report),
        "generated_at": str(report.get("generated_at", fields.MISSING)),
        # Never derived from the report: both this file and the page serving it are the
        # submitter's, so no report field could raise this above self-reported.
        "verification": "self-reported",
    }
    instrument = fields.instrument_sha256(report)
    if instrument:
        block["instrument_sha256"] = instrument
    model = fields.model(report)
    if model:
        block["model"] = model
    return block


# The neutral badge. Says a check ran and nothing else.
#
# WHY IT EXISTS. The result badge advertises a leak on the project's own README, and a
# maintainer who does not want that publishes no badge at all, which helps nobody: the
# check still ran and a reader still has no way to know. This says the true and much
# weaker thing instead.
#
# DELIBERATELY NOT GREEN. Green is the pass signal on every badge anybody has ever read,
# and a neutral badge coloured green would be a leaking gateway wearing a pass. Blue reads
# as informational, which is exactly what this is.
_NEUTRAL_BADGE: tuple[str, str] = ("benchmarked", "blue")


def build_badge(
    report: dict[str, Any],
    *,
    label: str = DEFAULT_LABEL,
    style: str = "result",
) -> dict[str, Any]:
    """Render one conformance report as a Shields.io endpoint payload.

    The returned dict is the whole file: Shields reads `schemaVersion`, `label`,
    `message` and `color` and ignores the rest.

    `style="neutral"` publishes that a check ran without publishing what it found. What it
    asserts is true and small: this project runs the check. It does not assert a pass, and
    the colour is chosen so it cannot be mistaken for one.
    """
    if style == "neutral":
        message, color = _NEUTRAL_BADGE
        return {
            "schemaVersion": 1,
            "label": label,
            "message": message,
            "color": color,
            "pii_leak_benchmark": {
                **_provenance(report, report.get("verdict")),
                "style": "neutral",
                # Stated in the file itself, because the badge no longer carries it and
                # somebody reading the JSON should not have to infer what was withheld.
                "note": (
                    "This badge says a conformance check ran. It does not say what the "
                    "check found. The run's own report holds the result."
                ),
            },
        }

    # The operator verdict wins where there is one, because on that report shape
    # `outcome` is not a verdict about the gateway at all. See `_VERDICT_BADGE`.
    verdict = report.get("verdict")
    if isinstance(verdict, str) and verdict:
        message, color = _VERDICT_BADGE.get(verdict.upper(), _UNKNOWN_BADGE)
        leaking = verdict.upper() == "LEAK"
    else:
        outcome = report.get("outcome")
        message, color = _OUTCOME_BADGE.get(str(outcome), _UNKNOWN_BADGE)
        leaking = outcome == "fail"

    # Only a measured leak gets the detail appended. A grey result that happens to
    # carry a stale count must not be dressed up as a finding, and a pass must stay the
    # single word a reader can take in at badge size.
    if leaking:
        detail = _leak_detail(report)
        if detail:
            message = detail

    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
        # Not read by Shields. Present so the published file is self-describing: a
        # reader who opens the JSON directly sees which instrument produced it and
        # that nobody verified it, rather than four fields of decoration.
        "pii_leak_benchmark": _provenance(report, verdict),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pii-leak-benchmark badge",
        description=(
            "Write a Shields.io endpoint JSON file for a conformance report, so a "
            "README badge shows the result rather than whether the workflow exited 0."
        ),
        epilog=(
            "Publish the file from your own repository, then point Shields at it: "
            "https://img.shields.io/endpoint?url=<raw URL of the published file>"
        ),
    )
    parser.add_argument(
        "report",
        nargs="?",
        default="./PII_LEAK_BENCHMARK_LATEST.json",
        help="Report JSON to render (default: ./PII_LEAK_BENCHMARK_LATEST.json).",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT,
        help=f"Where to write the badge JSON (default: {DEFAULT_OUTPUT}). Use - for stdout.",
    )
    parser.add_argument(
        "--style",
        choices=("result", "neutral"),
        default="result",
        help=(
            "result: say what the check found. neutral: say only that it ran, for a "
            "project that does not want its README advertising a leak. Neutral asserts "
            "less rather than something friendlier, and is never green."
        ),
    )
    parser.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help=f"Left-hand text on the badge (default: {DEFAULT_LABEL!r}).",
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

    badge = build_badge(report, label=args.label, style=args.style)
    rendered = json.dumps(badge, indent=2, ensure_ascii=False) + "\n"

    if args.out == "-":
        sys.stdout.write(rendered)
        return 0

    try:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    except OSError as exc:
        print(f"Cannot write badge: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {args.out} ({badge['message']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
