"""``pii-leak-benchmark`` -- the console entry point.

Requires only stdlib and ``httpx`` to ensure independent installation.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional, Sequence

DESCRIPTION = (
    "Measure whether an OpenAI-compatible streaming gateway sends raw personal data "
    "to its configured upstream, and whether it restores the values in the response."
)


def _target_headers_from_env() -> list[str]:
    """Parse one NAME=VALUE target header per non-empty environment line."""
    return [
        line
        for line in os.getenv("CONFORMANCE_TARGET_HEADERS", "").splitlines()
        if line.strip()
    ]

EPILOG = """\
This command publishes a comparative row requiring vendor claims and citations.
Other subcommands:
  selfcheck : Run without publishing (no claim flags needed).
  ci        : Run as a pipeline step (starts gateway, runs control, writes summary).
  cite      : Generate a citation block for a finished report.
  submit    : Open a public submission with the citation block.

The gateway under test MUST route upstream traffic to the capture server.
"""


def build_parser(
    prog: str = "pii-leak-benchmark",
    require_target: bool = True,
) -> argparse.ArgumentParser:
    """Build the argument parser. ``require_target`` is False for multi-profile callers."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target-base-url",
        default=None,
        required=require_target,
        help="OpenAI-compatible /v1 base URL of the gateway under test. "
        "Use capture://self for the raw pass-through negative control.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Measured iterations (default 3). Every iteration issues live requests to "
        "the target; raise it deliberately.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Machine-readable report path (default: ./PII_LEAK_BENCHMARK_LATEST.json).",
    )
    parser.add_argument(
        "--target-api-key",
        default=os.getenv("CONFORMANCE_TARGET_API_KEY", "conformance-key"),
        help="Target credential (default: CONFORMANCE_TARGET_API_KEY or a synthetic local value).",
    )
    parser.add_argument("--target-model", default="conformance-model", help="Model name sent to the target gateway.")
    parser.add_argument("--target-name", default="external-openai-compatible-endpoint")
    parser.add_argument("--target-version", default="unspecified")
    parser.add_argument(
        "--target-header",
        action="append",
        default=_target_headers_from_env(),
        metavar="NAME=VALUE",
        help="Additional request header (NAME=VALUE); repeat as needed. Not written to report. "
        "Prefer CONFORMANCE_TARGET_HEADERS for credentials (argv is visible in process listings).",
    )
    parser.add_argument(
        "--capture-host",
        default="127.0.0.1",
        help="Controlled upstream bind host. Anything other than loopback also requires "
        "--capture-public-url and --capture-token.",
    )
    parser.add_argument("--capture-port", type=int, default=8765, help="Controlled upstream bind port.")
    parser.add_argument(
        "--capture-public-url",
        default=os.getenv("CONFORMANCE_CAPTURE_PUBLIC_URL") or None,
        metavar="URL",
        help="Externally reachable /v1 base URL of the capture. Required if --capture-host "
        "is not loopback (a wildcard bind has no routable address).",
    )
    parser.add_argument(
        "--capture-token",
        default=None,
        metavar="TOKEN",
        help="Bearer token required by the capture in public mode. Prefer CONFORMANCE_CAPTURE_TOKEN "
        "over argv flags to prevent credential exposure in process listings.",
    )
    parser.add_argument(
        "--redaction-claimed",
        choices=["claimed", "not-offered", "unknown"],
        default="unknown",
        help="Vendor's PII redaction claim. 'not-offered' (for routing/caching gateways) yields "
        "not-applicable. Default 'unknown' yields claim-unstated. Neither is publishable as a failure.",
    )
    parser.add_argument(
        "--redaction-claim-citation",
        default=None,
        metavar="URL",
        help="Where the vendor states it. Required unless --redaction-claimed is unknown.",
    )
    parser.add_argument(
        "--redaction-claim-quote",
        default=None,
        metavar="TEXT",
        help="Optional short quote from the cited source.",
    )
    parser.add_argument(
        "--redaction-enabled",
        action="store_true",
        help="Target's redaction was enabled. Without this, runs against redacting products yield "
        "redaction-not-enabled (a configuration state, not a verdict).",
    )
    parser.add_argument(
        "--redaction-config-reference",
        default=None,
        metavar="TEXT",
        help="The exact setting/guardrail/config that enabled redaction. Required with "
        "--redaction-enabled to ensure reproducibility.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def headers_from_args(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in args.target_header:
        if "=" not in item:
            raise ValueError("--target-header must use NAME=VALUE")
        name, value = item.split("=", 1)
        if not name.strip():
            raise ValueError("--target-header name must not be empty")
        headers[name.strip()] = value
    return headers


def redaction_claim_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Construct the claim block, which drives the derived ``outcome``."""
    claim: dict[str, Any] = {
        "vendor_claims_pii_redaction": args.redaction_claimed,
        "configured_for_this_run": bool(args.redaction_enabled),
    }
    if args.redaction_claim_citation:
        claim["claim_citation"] = args.redaction_claim_citation
    if args.redaction_claim_quote:
        claim["claim_quote"] = args.redaction_claim_quote
    if args.redaction_config_reference:
        claim["configuration_reference"] = args.redaction_config_reference
    return claim


def run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the HTTP profile from arguments and return the report."""
    from pii_leak_benchmark.http_profile import run_http_conformance

    # Environment token wins over argv to prevent process listing exposure.
    capture_token = os.getenv("CONFORMANCE_CAPTURE_TOKEN") or args.capture_token

    return run_http_conformance(
        args.target_base_url,
        api_key=args.target_api_key,
        model=args.target_model,
        implementation_name=args.target_name,
        implementation_version=args.target_version,
        iterations=args.iterations if args.iterations is not None else 3,
        timeout_seconds=args.timeout_seconds,
        capture_host=args.capture_host,
        capture_port=args.capture_port,
        capture_token=capture_token,
        capture_public_url=args.capture_public_url,
        extra_headers=headers_from_args(args),
        redaction_claim=redaction_claim_from_args(args),
    )


def print_summary(report: dict[str, Any], destination: str) -> None:
    print(f"Report written to {destination}")
    print(f"  Passed:       {report['passed']}")
    print(f"  Outcome:      {report['outcome']}")
    if report["outcome"] not in ("pass", "fail"):
        # Ensure rationale is printed to prevent misinterpreting non-verdicts as failures.
        print(f"                {report['outcome_rationale']}")
    print(f"  Checks:       {len(report['checks'])}")
    print(f"  Iterations:   {report['checks']['client_observed_latency']['iterations']}")
    print("  Timing scope: client -> target -> controlled capture upstream -> target -> client")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    print("DEBUG MAIN:", arguments, argv, sys.argv, file=sys.stderr)
    if arguments and arguments[0] == "ci":
        from pii_leak_benchmark.ci import main as ci_main

        return ci_main(arguments[1:])

    # Dispatch subcommands early to bypass the main publisher parsing.
    if arguments and arguments[0] == "selfcheck":
        from pii_leak_benchmark.selfcheck import main as selfcheck_main

        return selfcheck_main(arguments[1:])

    # `cite` generates a citation block from a report.
    if arguments and arguments[0] == "cite":
        from pii_leak_benchmark.cite import main as cite_main

        return cite_main(arguments[1:])

    # `submit` opens a public submission using the citation block (never the full report).
    if arguments and arguments[0] == "submit":
        from pii_leak_benchmark.submit import main as submit_main

        return submit_main(arguments[1:])

    # `badge` generates a Shields.io JSON endpoint from a report.
    if arguments and arguments[0] == "badge":
        from pii_leak_benchmark.badge import main as badge_main

        return badge_main(arguments[1:])

    args = build_parser().parse_args(arguments)
    from pii_leak_benchmark.artifact import write_conformance_report

    try:
        report = run_from_args(args)
        destination = write_conformance_report(
            report, args.json_out or "./PII_LEAK_BENCHMARK_LATEST.json"
        )
    except (OSError, ValueError) as exc:
        # Catch connection/file errors to prevent stack traces on unreachable captures.
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2

    print_summary(report, destination)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
