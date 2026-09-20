"""``pii-leak-benchmark selfcheck`` -- the operator smoke test.

Answers "does my deployment leak?" without the friction of the publishable `flat` command
(which requires vendor claims and citations).

*   `outcome` (Publishable Row): Remains `claim-unstated` (fail-closed, not publishable).
*   `verdict` (Operator Action): Defined here to provide actionable feedback.

Crucially, this command refuses to return CLEAN if traffic never reached the capture (e.g.,
a misconfigured proxy), returning NOT MEASURED (exit 2) instead. Every needle check passing
because nothing was inspected is not a clean bill of health.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional, Sequence

from pii_leak_benchmark import explain
from pii_leak_benchmark.explain import OperatorSpecimens

DESCRIPTION = (
    "Smoke-test your own gateway: does it send raw personal data to its upstream, and "
    "does it give the values back to the client? No vendor claim required."
)

EPILOG = """\
Your gateway must ALREADY be configured to send its upstream traffic to the capture this
command starts (default http://127.0.0.1:8765/v1). This command never reconfigures your
gateway -- a harness that could reconfigure the thing it measures could also configure it
to pass.

Exit status:
  0  CLEAN        every check passed and the run was attributable
  1  LEAK / CHECK FAILED  observed leakage or a separate behavioural failure
  2  NOT MEASURED nothing reached the capture, or the run could not be trusted

Establish the floor first. With no gateway at all, this MUST report LEAK:

  pii-leak-benchmark selfcheck --target-base-url capture://self

If that reports anything else, your capture is not seeing traffic and no other run from
this setup means anything.

The report this writes is NOT publishable as a row about a product: it records no vendor
claim, so its outcome is claim-unstated by design. To publish a comparative result, use
the flat command and record the claim.
"""

# Re-derive attributable/leaked from the report to prevent the selfcheck verdict from
# drifting from the outcome derivation without tests noticing.
_BOUNDARY = "configured_upstream_boundary"

# Named here rather than imported at module scope: `cli` imports this module, and
# `http_profile` is the heavy import the flat command already defers.
_CREDENTIAL_TYPES = ("AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "SLACK_TOKEN")

VERDICT_CLEAN = "CLEAN"
VERDICT_LEAK = "LEAK"
VERDICT_CHECK_FAILED = "CHECK FAILED"
VERDICT_NOT_MEASURED = "NOT MEASURED"

EXIT_CLEAN = 0
EXIT_LEAK = 1
EXIT_NOT_MEASURED = 2


def build_parser(prog: str = "pii-leak-benchmark selfcheck") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target-base-url",
        required=True,
        metavar="URL",
        help="Your gateway's OpenAI-compatible /v1 base URL. Use capture://self for the "
        "no-gateway negative control, which must report LEAK.",
    )
    parser.add_argument(
        "--target-api-key",
        default=os.getenv("CONFORMANCE_TARGET_API_KEY"),
        help="Bearer token for your gateway. Env: CONFORMANCE_TARGET_API_KEY, which is "
        "preferred because process listings expose argv.",
    )
    parser.add_argument("--target-model", default="conformance-model")
    # Deferred: `cli` imports this module to dispatch the subcommand, so importing it at
    # module scope is circular. `build_parser` runs long after both are loaded.
    from pii_leak_benchmark.cli import _target_headers_from_env

    parser.add_argument(
        "--target-header",
        action="append",
        # Uses the same default as `flat` so `append` adds to the environment rather than
        # replacing it. A `None` default would silently discard CONFORMANCE_TARGET_HEADERS
        # (credentials/routing) when a single CLI header is provided, causing unauthenticated
        # requests and false NOT MEASURED reports.
        default=_target_headers_from_env(),
        metavar="NAME=VALUE",
        help="Additional request header; repeat as needed. Values are not written to the "
        "report. Prefer newline-delimited CONFORMANCE_TARGET_HEADERS for credentials.",
    )
    parser.add_argument("--capture-host", default="127.0.0.1")
    parser.add_argument("--capture-port", type=int, default=8765)
    parser.add_argument(
        "--capture-public-url",
        default=os.getenv("CONFORMANCE_CAPTURE_PUBLIC_URL") or None,
        metavar="URL",
        help="Externally reachable /v1 base URL your gateway will be pointed at -- your "
        "tunnel or VPS. Required whenever --capture-host is not loopback. Use this to "
        "measure a hosted or production gateway that cannot reach your laptop.",
    )
    parser.add_argument(
        "--capture-token",
        default=None,
        metavar="TOKEN",
        help="Bearer token the capture requires. Required in public mode. Prefer the "
        "CONFORMANCE_CAPTURE_TOKEN environment variable.",
    )
    parser.add_argument(
        "--no-credentials",
        action="store_true",
        help="Send only the three personal-data types, matching the published profile. "
        "By default the check also sends AWS, GitHub and Slack credential specimens.",
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--duty", choices=("restore", "anonymize"), default="restore",
                        help="Whether your gateway must restore values or only anonymize requests.")
    parser.add_argument("--profile", choices=("pii-v1", "pii-secrets-v1"), default="pii-secrets-v1",
                        help="Explicit smoke-test coverage; published paper profiles are separate.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--json-out",
        default=None,
        metavar="PATH",
        help="Also write the full JSON report here. Not publishable as a row: it records "
        "no vendor claim, so its outcome is claim-unstated.",
    )
    return parser


def verdict_for(report: dict[str, Any], *, duty: str = "restore") -> tuple[str, str]:
    """The OPERATOR's reading of the run. Returns (verdict, one-line reason).

    Attributability is checked first. If a run never reached the capture, it inspected
    nothing and passed vacuously; returning CLEAN here would actively mislead the operator.
    """
    boundary = report["checks"][_BOUNDARY]

    if not boundary["correlated_requests"]:
        return (
            VERDICT_NOT_MEASURED,
            "No request carrying this run's marker reached the capture. Your gateway is "
            "not configured to use it as its upstream, could not reach it, or sent the "
            "traffic elsewhere -- these are indistinguishable here.",
        )

    if boundary["uninspectable_requests"] or boundary["unattributed_uninspectable_requests"]:
        return (
            VERDICT_NOT_MEASURED,
            "Part of the traffic could not be safely inspected, so the run fails closed "
            "rather than assuming nothing leaked. See uninspectable_reasons.",
        )

    leaked = sorted(
        set(boundary["leaked_entity_types"])
        | set(boundary["unattributed_leaked_entity_types"])
    )
    if leaked:
        return (
            VERDICT_LEAK,
            "Raw fixture values reached the upstream: " + ", ".join(leaked) + ".",
        )

    ignored = {"response_fidelity", "fragmentation_safety"} if duty == "anonymize" else set()
    if duty not in {"restore", "anonymize"}:
        raise ValueError("duty must be restore or anonymize")
    failed = sorted(name for name, check in report["checks"].items()
                    if name not in ignored and not check.get("passed", True))
    if failed:
        return (
            VERDICT_CHECK_FAILED,
            "Nothing leaked upstream, but these checks did not pass: "
            + ", ".join(failed)
            + ". A gateway that masks without restoring is not leaking -- it is breaking "
            "the response. Read the report before treating it as a privacy failure.",
        )

    return (VERDICT_CLEAN, f"No fixture value reached the upstream; required {duty} checks passed.")


def _print_per_entity(report: dict[str, Any], boundary: dict[str, Any]) -> None:
    """Prints one row per entity TESTED.

    Printing only leaks makes a clean run unreadable, as it fails to distinguish between
    "SSN was tested and contained" and "SSN was never tested".
    """
    tested = sorted(report.get("fixture", {}).get("formats", {}))
    if not tested:
        return
    leaked = set(boundary["leaked_entity_types"]) | set(
        boundary["unattributed_leaked_entity_types"]
    )
    attributable = bool(boundary["correlated_requests"])
    complete = attributable and not (
        boundary["uninspectable_requests"] or boundary["unattributed_uninspectable_requests"]
    )

    # Sized from the data, not a constant: AWS_ACCESS_KEY_ID is 17 characters and ran
    # straight into the RESULT column at a hardcoded 16.
    width = max(len(entity) for entity in tested) + 2
    print("  Data types tested")
    print(f"    {'TYPE':<{width}}{'RESULT':<14}WHAT IT MEANS")
    for entity in tested:
        if not attributable:
            # NOT "contained". Nothing was inspected, so nothing was contained -- saying
            # otherwise reintroduces per row the false assurance the verdict ordering
            # exists to prevent.
            state, meaning = "not measured", "no traffic from your gateway was inspected"
        elif entity in leaked:
            state, meaning = "LEAK", "sent to the upstream unmasked"
        elif not complete:
            state, meaning = "not measured", "some traffic could not be inspected"
        else:
            state, meaning = "contained", "never reached the upstream in this run"
        print(f"    {entity:<{width}}{state:<14}{meaning}")
    print()
    print("  Not tested by this profile: every format beyond the rows above.")
    print("    Do not infer broad coverage of health and clinical data, government IDs,")
    print("    names, addresses, private keys or connection strings from these rows.")
    if not any(entity in tested for entity in _CREDENTIAL_TYPES):
        print("    API keys, tokens and other credentials were not sent.")
    if any(entity in tested for entity in _CREDENTIAL_TYPES):
        print("    - credential formats beyond the three fixed synthetic specimens")
    print()


def findings_for(
    report: dict[str, Any],
    specimens: Optional["OperatorSpecimens"] = None,
    *,
    duty: str = "restore",
) -> list[explain.Finding]:
    """What this run should SAY, under the duty the operator declared.

    Maintained as a named wrapper so the duty logic (e.g. preventing anonymizing gateways
    from being told their values didn't return on a CLEAN run) can be tested directly.
    """
    return explain.findings_from_report(report, specimens, duty=duty)


def _print_report(
    report: dict[str, Any],
    verdict: str,
    reason: str,
    destination: Optional[str],
    specimens: Optional["OperatorSpecimens"] = None,
    duty: str = "restore",
) -> None:
    boundary = report["checks"][_BOUNDARY]
    capture = report["capture"]

    print()
    print(f"Target:  {report['implementation']['name']}")
    print(f"Capture: {capture['target_must_be_preconfigured_for']}")
    print()
    print(f"  {verdict}")
    print()
    for line in _wrap(reason, 76):
        print(f"  {line}")
    print()
    print(
        f"  Requests to capture: {boundary['captured_requests']}"
        f"  |  correlated to this run: {boundary['correlated_requests']}"
        f"  |  uninspectable: {boundary['uninspectable_requests']}"
    )
    print()

    _print_per_entity(report, boundary)

    # Print the synthetic specimens (`reveal=True`) above the matcher table. Showing the
    # actual generated values makes the failure actionable, whereas the matcher table
    # ("EMAIL / literal / body") only explains how the finding was produced.
    explain.print_findings(
        findings_for(report, specimens, duty=duty), reveal=specimens is not None
    )

    evidence = boundary["leak_evidence"] + boundary["unattributed_leak_evidence"]
    if evidence:
        print("  How it leaked")
        print(f"    {'ENTITY':<14}{'MATCH':<12}{'SCOPE':<14}CHANNEL")
        for item in sorted(evidence, key=lambda e: (e["entity_type"], e["channel"])):
            print(
                f"    {item['entity_type']:<14}{item['match']:<12}"
                f"{item['scope']:<14}{item['channel']}"
            )
        print()
        print("    A 'literal' match is the value verbatim. A 'normalized' match was")
        print("    recovered only after joining fragments and stripping separators.")
        print()

    print("  Checks")
    for name in sorted(report["checks"]):
        state = "pass" if report["checks"][name].get("passed", True) else "FAIL"
        print(f"    {name:<32}{state}")
    print()

    if destination:
        print(f"  Full report: {destination}")
    print(
        "  This measures your deployment. It is not a publishable verdict about a "
        "product\n  (outcome: "
        f"{report['outcome']}); to publish a row, use the flat command and record the claim."
    )
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    from pii_leak_benchmark.artifact import write_conformance_report
    from pii_leak_benchmark.cli import headers_from_args
    from pii_leak_benchmark.http_profile import run_http_conformance

    args = build_parser().parse_args(argv)
    # Passed separately from the report to prevent the generated synthetic specimens
    # from leaking into the on-disk JSON artifact.
    specimens = OperatorSpecimens()

    try:
        report = run_http_conformance(
            args.target_base_url,
            api_key=args.target_api_key,
            model=args.target_model,
            implementation_name=args.target_base_url,
            implementation_version="selfcheck",
            iterations=args.iterations,
            timeout_seconds=args.timeout_seconds,
            capture_host=args.capture_host,
            capture_port=args.capture_port,
            capture_token=os.getenv("CONFORMANCE_CAPTURE_TOKEN") or args.capture_token,
            capture_public_url=args.capture_public_url,
            extra_headers=headers_from_args(args),
            # Credentials are ON by default here (unlike the `flat` command) because
            # operator self-checks typically care about secrets as much as personal data.
            # This doesn't corrupt historical comparisons because selfchecks are not
            # publishable rows.
            include_credentials=args.profile == "pii-secrets-v1" and not args.no_credentials,
            # Force `claim-unstated`. This explicitly declines participation in the
            # publishable-row machinery.
            redaction_claim=None,
            specimens=specimens,
        )
    except (OSError, ValueError) as exc:
        print(f"\n  {VERDICT_NOT_MEASURED}\n", file=sys.stderr)
        print(f"  The run could not be trusted: {exc}", file=sys.stderr)
        print(
            "\n  This is not a clean result. Fix the condition above and re-run.\n",
            file=sys.stderr,
        )
        return EXIT_NOT_MEASURED

    destination = None
    if args.json_out:
        # Caught here to prevent an unwritable path (OSError) from uncaught-exiting with 1,
        # which this command documents as LEAK. A missing directory should not read as a leak.
        try:
            destination = write_conformance_report(report, args.json_out)
        except (OSError, ValueError) as exc:
            print(f"\n  {VERDICT_NOT_MEASURED}\n", file=sys.stderr)
            print(
                f"  The measurement ran, but its report could not be written: {exc}",
                file=sys.stderr,
            )
            print(
                "\n  Treat this as no measurement. Fix the path and re-run.\n",
                file=sys.stderr,
            )
            return EXIT_NOT_MEASURED

    verdict, reason = verdict_for(report, duty=args.duty)
    _print_report(report, verdict, reason, destination, specimens=specimens, duty=args.duty)

    if verdict == VERDICT_CLEAN:
        return EXIT_CLEAN
    if verdict in (VERDICT_LEAK, VERDICT_CHECK_FAILED):
        return EXIT_LEAK
    return EXIT_NOT_MEASURED
