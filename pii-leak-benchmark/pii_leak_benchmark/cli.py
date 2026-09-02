"""``pii-leak-benchmark`` -- the console entry point.

This ran as ``llm-shield-proxy benchmark --target-base-url ...`` and then as
``llm-shield-conformance``. Both names put the reference proxy's name on the neutral
measurer, which is the one thing a referee cannot afford. The command, the
distribution and the import package are now named after what is measured.

The module imports standard library plus ``httpx`` and nothing else, so measuring a
gateway never requires installing another one.
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

EPILOG = """\
The gateway under test must already be configured to send its upstream traffic to the
capture this command starts (default http://127.0.0.1:8765/v1). Nothing is measured
about a gateway that never reaches the capture: that run reports
outcome=inconclusive, which is not a verdict and must not be published as one.

Example:

  pii-leak-benchmark --target-base-url http://127.0.0.1:8899/v1 \\
      --target-name some-gateway --target-version 1.2.3 \\
      --redaction-claimed claimed --redaction-claim-citation https://vendor.example/docs \\
      --redaction-enabled --redaction-config-reference "guardrail: pii, redact: true"
"""


def build_parser(
    prog: str = "pii-leak-benchmark",
    require_target: bool = True,
) -> argparse.ArgumentParser:
    """The HTTP-profile parser.

    ``require_target`` is False for callers that also offer another profile on the
    same command line and decide the branch themselves.
    """
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
        default=[],
        metavar="NAME=VALUE",
        help="Additional target request header; repeat as needed. Values are not written to the report.",
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
        help="Externally reachable /v1 base URL the target will be configured with -- your "
        "tunnel or VPS. Required whenever --capture-host is not loopback, because a "
        "wildcard bind has no address a target can connect to. Env: "
        "CONFORMANCE_CAPTURE_PUBLIC_URL.",
    )
    parser.add_argument(
        "--capture-token",
        default=None,
        metavar="TOKEN",
        help="Bearer token the capture requires, so arbitrary internet traffic cannot enter "
        "the capture record. Required in public mode. PREFER the CONFORMANCE_CAPTURE_TOKEN "
        "environment variable: process listings show argv, so a token passed as a flag is "
        "visible to every other user on the host. The token is never written to the report.",
    )
    parser.add_argument(
        "--redaction-claimed",
        choices=["claimed", "not-offered", "unknown"],
        default="unknown",
        help="What the target's vendor CLAIMS about PII redaction. 'not-offered' marks a "
        "product that does not advertise redaction at all (caching/routing/observability "
        "gateways); its run is reported as not-applicable and MUST NOT be published as a "
        "failure. Default 'unknown' yields outcome=claim-unstated, which is not publishable.",
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
        help="The target's redaction feature was enabled for this run. Without it a run "
        "against a redacting product is reported as redaction-not-enabled: a configuration "
        "statement, not a verdict.",
    )
    parser.add_argument(
        "--redaction-config-reference",
        default=None,
        metavar="TEXT",
        help="The exact setting/guardrail/config that enabled redaction. Required with "
        "--redaction-enabled so the row reproduces.",
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
    """The claim block. The harness DERIVES ``outcome`` from it; it is never a free string."""
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
    """Run the HTTP profile described by parsed arguments and return the report."""
    from pii_leak_benchmark.http_profile import run_http_conformance

    # Environment first. A token in argv is readable from any process listing on the
    # host; the flag stays for scripted use but the env var wins.
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
        # Say it on stdout too. A reader who never opens the JSON must not write a
        # "Fail" row from a run that was never a verdict.
        print(f"                {report['outcome_rationale']}")
    print(f"  Checks:       {len(report['checks'])}")
    print(f"  Iterations:   {report['checks']['client_observed_latency']['iterations']}")
    print("  Timing scope: client -> target -> controlled capture upstream -> target -> client")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from pii_leak_benchmark.artifact import write_conformance_report

    try:
        report = run_from_args(args)
        destination = write_conformance_report(
            report, args.json_out or "./PII_LEAK_BENCHMARK_LATEST.json"
        )
    except (OSError, ValueError) as exc:
        # CaptureUnreachableError subclasses OSError deliberately, so a hijacked or
        # unreachable capture lands here rather than as a traceback.
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2

    print_summary(report, destination)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
