"""``pii-leak-benchmark selfcheck`` -- the operator smoke test.

WHY THIS EXISTS. The flat command is built for PUBLISHING a comparative row, so it
requires the vendor's claim, a citation for it, and the exact setting that enabled
redaction. Those flags are what stop a published table from saying "Fail" about a product
that never offered redaction, and they are not negotiable there.

They are pure friction for the other audience: an operator pointing the harness at their
own gateway to answer one question -- *does my deployment leak?* There is no vendor to
cite. It is their own proxy.

WHAT IT DOES NOT DO. It does not weaken the claim machinery; it declines to participate in
it. The run records no claim, so `outcome` derives to `claim-unstated` exactly as it would
from the flat command with no claim flags -- the fail-closed default, not publishable as a
verdict about anyone. What this module adds is a second, separate reading of the same
measurement, addressed to the person who owns the deployment:

    outcome   -- what a PUBLISHED ROW may say about a product. Unchanged, still derived.
    verdict   -- what the OPERATOR should do about their own gateway. Defined here.

Those answer different questions and are deliberately not the same field. A selfcheck can
say LEAK while `outcome` says `claim-unstated`, and both are correct: raw values reached
the upstream, and you have not written down enough for that to be a publishable finding
about a product.

THE TRAP THIS IS BUILT AROUND. The dangerous result is not a leak, which is loud. It is a
run whose traffic never reached the capture at all: every needle check trivially passes
because nothing was ever inspected, and an operator reads "no leak" and ships. The harness
already refuses to call that a pass -- `attributable` gates the outcome -- and this command
refuses harder, reporting NOT MEASURED and exit 2. Never let a quiet misconfiguration read
as a clean bill of health.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional, Sequence

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
  1  LEAK         raw values reached the upstream, or a behavioural check failed
  2  NOT MEASURED nothing reached the capture, or the run could not be trusted

Establish the floor first. With no gateway at all, this MUST report LEAK:

  pii-leak-benchmark selfcheck --target-base-url capture://self

If that reports anything else, your capture is not seeing traffic and no other run from
this setup means anything.

The report this writes is NOT publishable as a row about a product: it records no vendor
claim, so its outcome is claim-unstated by design. To publish a comparative result, use
the flat command and record the claim.
"""

# Re-derived from the report rather than re-measured: `attributable` is
# `bool(correlated)` and `leaked` is `bool(leaked_types or unattributed_leaked_types)`
# in http_profile. Keeping these as accessors means the selfcheck verdict cannot drift
# from the outcome derivation without a test noticing.
_BOUNDARY = "configured_upstream_boundary"

VERDICT_CLEAN = "CLEAN"
VERDICT_LEAK = "LEAK"
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
    parser.add_argument(
        "--target-header",
        action="append",
        default=None,
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
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--json-out",
        default=None,
        metavar="PATH",
        help="Also write the full JSON report here. Not publishable as a row: it records "
        "no vendor claim, so its outcome is claim-unstated.",
    )
    return parser


def verdict_for(report: dict[str, Any]) -> tuple[str, str]:
    """The OPERATOR's reading of the run. Returns (verdict, one-line reason).

    Deliberately ordered attributability first. A run that never reached the capture
    inspected nothing, so every needle check passed vacuously; reporting that as CLEAN
    would be the one failure mode that actively misleads someone into shipping.
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

    if not report["passed"]:
        failed = sorted(
            name for name, check in report["checks"].items() if not check.get("passed", True)
        )
        return (
            VERDICT_LEAK,
            "Nothing leaked upstream, but these checks did not pass: "
            + ", ".join(failed)
            + ". A gateway that masks without restoring is not leaking -- it is breaking "
            "the response. Read the report before treating it as a privacy failure.",
        )

    return (VERDICT_CLEAN, "No fixture value reached the upstream and every check passed.")


def _print_report(report: dict[str, Any], verdict: str, reason: str, destination: Optional[str]) -> None:
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

    evidence = boundary["leak_evidence"] + boundary["unattributed_leak_evidence"]
    if evidence:
        print("  What leaked")
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
    if args.target_header is None:
        args.target_header = [
            line
            for line in os.getenv("CONFORMANCE_TARGET_HEADERS", "").splitlines()
            if line.strip()
        ]

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
            # No claim, on purpose. See the module docstring: this declines to
            # participate in the publishable-row machinery rather than weakening it.
            redaction_claim=None,
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
        destination = write_conformance_report(report, args.json_out)

    verdict, reason = verdict_for(report)
    _print_report(report, verdict, reason, destination)

    if verdict == VERDICT_CLEAN:
        return EXIT_CLEAN
    if verdict == VERDICT_LEAK:
        return EXIT_LEAK
    return EXIT_NOT_MEASURED
