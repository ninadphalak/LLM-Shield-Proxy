"""Compliance & Administrative CLI for LLM-Shield-Proxy.

Provides operator-facing subcommands that do not launch the ASGI server, such
as bundling auditor-ready compliance packs from WORM audit evidence and OSCAL
assessment artifacts.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence



def build_compliance_report_parser() -> argparse.ArgumentParser:
    # Deferred: compliance.report pulls the proxy's crypto stack, and the HTTP
    # conformance profile in this same module must stay installable without it.
    from llm_shield_proxy.compliance.report import SUPPORTED_FRAMEWORKS

    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy compliance-report",
        description="Generate a compliance evidence pack (OSCAL + audit verification + checksums).",
    )
    parser.add_argument(
        "--framework",
        required=True,
        choices=SUPPORTED_FRAMEWORKS,
        help="Compliance framework narrative to target.",
    )
    parser.add_argument(
        "--out",
        default="./compliance_pack.zip",
        help="Output .zip path for the compliance pack (default: %(default)s).",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to a hash-chained audit JSONL file. Omit to skip audit evidence.",
    )
    parser.add_argument(
        "--oscal-file",
        default=None,
        help=(
            "Path to a persisted OSCAL JSON/JSONL artifact (e.g. from the GRC sidecar file transport). "
            "Omit to generate an empty OSCAL shell."
        ),
    )
    parser.add_argument(
        "--pubkey-file",
        default=None,
        help="PEM file with the proxy's published Ed25519 audit public key (GET /api/v1/audit/pubkey), "
        "used to verify audit receipt signatures.",
    )
    return parser


def compliance_report_main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_compliance_report_parser()
    args = parser.parse_args(argv)

    from llm_shield_proxy.compliance.report import generate_compliance_pack

    result = generate_compliance_pack(
        framework=args.framework,
        out_path=args.out,
        audit_log_path=args.audit_log,
        oscal_file_path=args.oscal_file,
        pubkey_file_path=args.pubkey_file,
    )

    audit_summary = result["audit_summary"]
    oscal_summary = result["oscal_summary"]

    print(f"Compliance pack written to {result['out_path']}")
    print(f"  Framework:        {args.framework.upper()}")
    print(f"  Audit events:     {audit_summary['total_events']}")
    print(f"  Chain valid:      {audit_summary['chain_valid']}")
    print(f"  OSCAL results:    {oscal_summary['result_count']}")
    print(f"  Integrity files:  {len(result['checksums'])}")

    return 1 if audit_summary["chain_valid"] is False else 0


def build_assess_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy assess",
        description="Assess JSON/JSONL traffic locally and emit aggregate, privacy-safe reports.",
    )
    parser.add_argument("--input", required=True, help="JSON or JSONL file containing representative requests.")
    parser.add_argument("--out", default="./assessment", help="Output directory (default: %(default)s).")
    parser.add_argument(
        "--disable-tier2",
        action="store_true",
        help="Disable Shannon-entropy secret detection for this assessment.",
    )
    parser.add_argument(
        "--enable-tier3",
        action="store_true",
        help="Enable the configured local ONNX NER tier. No model is downloaded automatically.",
    )
    parser.add_argument(
        "--assessment-plan-href",
        default=None,
        help="URI reference to the OSCAL Assessment Plan governing this assessment.",
    )
    return parser


def assess_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_assess_parser().parse_args(argv)
    from llm_shield_proxy.assessment import run_assessment, write_assessment_report

    try:
        report = run_assessment(
            args.input,
            enable_tier2=not args.disable_tier2,
            enable_tier3=args.enable_tier3,
            assessment_plan_href=args.assessment_plan_href,
        )
        paths = write_assessment_report(report, args.out)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"Assessment failed: {exc}", file=sys.stderr)
        return 2

    print(f"Assessment written to {Path(args.out).resolve()}")
    print(f"  Records:          {report['source']['records']}")
    print(f"  Records flagged:  {report['findings']['records_with_findings']}")
    print(f"  Findings:         {report['findings']['total']}")
    print("  Source content:   not persisted")
    for artifact, path in sorted(paths.items()):
        print(f"  {artifact.upper():<16} {path}")
    return 0


def build_audit_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy audit-verify",
        description="Verify a hash-chained, Ed25519-signed audit JSONL file.",
    )
    parser.add_argument("--audit-log", required=True, help="Audit JSONL path.")
    parser.add_argument("--pubkey-file", default=None, help="Ed25519 public-key PEM path.")
    parser.add_argument("--json-out", default=None, help="Optional path for the machine-readable verification summary.")
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help=(
            "Exit 0 on hash continuity alone, without signature verification. "
            "Continuity is not authenticity: the record hash is an unkeyed SHA-256 "
            "that anyone can recompute over records they wrote themselves."
        ),
    )
    return parser


def audit_verify_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_audit_verify_parser().parse_args(argv)
    from llm_shield_proxy.compliance.report import verify_worm_log

    try:
        pubkey = Path(args.pubkey_file).read_text(encoding="utf-8") if args.pubkey_file else None
        result = verify_worm_log(args.audit_log, pubkey)
    except (OSError, ValueError) as exc:
        print(f"Audit verification failed: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        import json

        destination = Path(args.json_out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Audit events:       {result['total_events']}")
    print(f"Chain valid:        {result['chain_valid']}")
    print(f"Authenticity:       {'verified' if result['authenticity_verified'] else 'NOT VERIFIED'}")
    print(f"Integrity issues:   {len(result['chain_breaks'])}")
    print(f"Unsigned events:    {result['unsigned_events']}")
    if result["signature_checked"]:
        print(f"Valid signatures:   {result['signatures_valid']}")
        print(f"Invalid signatures: {result['signatures_invalid']}")
    # Deleting a suffix leaves a shorter but internally consistent chain. Print the
    # terminal state so it can be compared against an independently held anchor,
    # which is the only way truncation is detectable.
    print(f"Terminal sequence:  {result['last_sequence']}")
    print(f"Terminal hash:      {result['terminal_hash']}")
    print(f"Chain ids:          {', '.join(result['chain_ids_seen']) or '(none)'}")

    if not result["signature_checked"]:
        print(
            "WARNING: no --pubkey-file supplied. Hash continuity alone is not evidence of "
            "authenticity; anyone can recompute an unkeyed hash chain over records they "
            "wrote themselves. This result attests internal consistency only.",
            file=sys.stderr,
        )
    if result["unsigned_events"] and result["signature_checked"]:
        print(
            f"WARNING: {result['unsigned_events']} record(s) carry no signature.",
            file=sys.stderr,
        )
    print(
        "NOTE: records deleted from the END of a chain leave a valid shorter chain. "
        "Compare the terminal sequence and hash above against an independently held "
        "anchor to detect truncation.",
        file=sys.stderr,
    )

    if result["chain_valid"] is not True:
        return 1
    # Exit 0 is what automation reads. It must mean the log was verified against a
    # key, not merely that it is self-consistent.
    if result["authenticity_verified"] or args.allow_unsigned:
        return 0
    print(
        "FAILED: continuity holds but authenticity was not established. Supply "
        "--pubkey-file to verify signatures, or --allow-unsigned to accept a "
        "consistency-only result.",
        file=sys.stderr,
    )
    return 1


def build_audit_checkpoint_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy audit-checkpoint",
        description="Verify worker audit chains and create one signed terminal-state checkpoint.",
    )
    parser.add_argument(
        "--audit-log",
        required=True,
        action="append",
        help="Audit JSONL path. Repeat once per worker chain.",
    )
    parser.add_argument(
        "--audit-pubkey-file",
        required=True,
        help="Ed25519 public-key PEM used to verify every supplied worker chain.",
    )
    parser.add_argument(
        "--signing-key-file",
        required=True,
        help="Operator-controlled Ed25519 private key used only to sign the checkpoint manifest.",
    )
    parser.add_argument("--out", default="./audit-checkpoint.json", help="Checkpoint output path.")
    return parser


def audit_checkpoint_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_audit_checkpoint_parser().parse_args(argv)
    from llm_shield_proxy.compliance.evidence import (
        build_audit_checkpoint,
        load_ed25519_private_key_file,
        write_audit_checkpoint,
    )

    try:
        audit_public_key = Path(args.audit_pubkey_file).read_text(encoding="utf-8")
        checkpoint_key = load_ed25519_private_key_file(args.signing_key_file)
        checkpoint = build_audit_checkpoint(args.audit_log, audit_public_key, checkpoint_key)
        destination = write_audit_checkpoint(checkpoint, args.out)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Audit checkpoint failed: {exc}", file=sys.stderr)
        return 2

    print(f"Audit checkpoint written to {destination}")
    print(f"  Worker chains: {checkpoint['total_chains']}")
    print(f"  Audit events:  {checkpoint['total_events']}")
    print(f"  Manifest hash: {checkpoint['manifest_hash']}")
    return 0


def build_checkpoint_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy audit-checkpoint-verify",
        description="Verify a signed audit terminal-state checkpoint.",
    )
    parser.add_argument("--checkpoint", required=True, help="Checkpoint JSON path.")
    parser.add_argument("--pubkey-file", required=True, help="Checkpoint-signing public-key PEM path.")
    return parser


def checkpoint_verify_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_checkpoint_verify_parser().parse_args(argv)
    from llm_shield_proxy.compliance.evidence import verify_audit_checkpoint

    try:
        import json

        checkpoint = json.loads(Path(args.checkpoint).read_text(encoding="utf-8"))
        public_key = Path(args.pubkey_file).read_text(encoding="utf-8")
        result = verify_audit_checkpoint(checkpoint, public_key)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Checkpoint verification failed: {exc}", file=sys.stderr)
        return 2

    print(f"Checkpoint valid: {result['valid']}")
    print(f"Worker chains:    {result['chains']}")
    return 0 if result["valid"] else 1


def build_benchmark_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy benchmark",
        description="Run the local implementation profile or an OpenAI-compatible HTTP gateway profile.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Measured iterations (default: 2000 local profile, 3 HTTP profile). The HTTP "
        "profile issues live requests to the target; raise it deliberately.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Machine-readable result path (default: CONFORMANCE_LATEST.json for the local "
        "profile, CONFORMANCE_HTTP_LATEST.json for the HTTP profile).",
    )
    parser.add_argument(
        "--target-base-url",
        default=None,
        help="OpenAI-compatible /v1 base URL. Omit for the local implementation profile; use capture://self for a raw baseline.",
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


def benchmark_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_benchmark_parser().parse_args(argv)

    from llm_shield_proxy.conformance import write_conformance_report

    http_profile = bool(args.target_base_url)
    if not http_profile:
        # The conformance harness is an offline test tool. Disable only configured
        # OpenTelemetry export before importing PIIEngine/tracing; do not alter the
        # product's anonymous usage tracking setting.
        #
        # Both of these imports pull the whole reference proxy, so they are scoped to
        # the LOCAL profile. The HTTP profile measures somebody else's gateway and must
        # not require this one to be installed at all -- and it imports no OTel
        # provider, so there is no exporter here to disable.
        from llm_shield_proxy.core.config import settings

        settings.TELEMETRY_ENABLED = False
        settings.TELEMETRY_ENDPOINT_URL = None
    iterations = args.iterations if args.iterations is not None else (3 if http_profile else 2_000)
    json_out = args.json_out or (
        "./CONFORMANCE_HTTP_LATEST.json" if http_profile else "./CONFORMANCE_LATEST.json"
    )

    try:
        if http_profile:
            headers = {}
            for item in args.target_header:
                if "=" not in item:
                    raise ValueError("--target-header must use NAME=VALUE")
                name, value = item.split("=", 1)
                if not name.strip():
                    raise ValueError("--target-header name must not be empty")
                headers[name.strip()] = value

            # Environment first. A token in argv is readable from any process listing
            # on the host; the flag stays for scripted use but the env var wins.
            capture_token = os.getenv("CONFORMANCE_CAPTURE_TOKEN") or args.capture_token

            # The harness derives `outcome` from this; it is never a free string.
            redaction_claim = {
                "vendor_claims_pii_redaction": args.redaction_claimed,
                "configured_for_this_run": bool(args.redaction_enabled),
            }
            if args.redaction_claim_citation:
                redaction_claim["claim_citation"] = args.redaction_claim_citation
            if args.redaction_claim_quote:
                redaction_claim["claim_quote"] = args.redaction_claim_quote
            if args.redaction_config_reference:
                redaction_claim["configuration_reference"] = args.redaction_config_reference

            from llm_shield_proxy.conformance.http_profile import run_http_conformance

            report = run_http_conformance(
                args.target_base_url,
                api_key=args.target_api_key,
                model=args.target_model,
                implementation_name=args.target_name,
                implementation_version=args.target_version,
                iterations=iterations,
                timeout_seconds=args.timeout_seconds,
                capture_host=args.capture_host,
                capture_port=args.capture_port,
                capture_token=capture_token,
                capture_public_url=args.capture_public_url,
                extra_headers=headers,
                redaction_claim=redaction_claim,
            )
        else:
            from llm_shield_proxy.conformance import run_conformance

            report = run_conformance(iterations)
        destination = write_conformance_report(report, json_out)
    except (OSError, ValueError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2

    print(f"Conformance report written to {destination}")
    print(f"  Passed:       {report['passed']}")
    if "outcome" in report:
        print(f"  Outcome:      {report['outcome']}")
        if report["outcome"] not in ("pass", "fail"):
            # Say it here too. A reader who only sees stdout must not write a
            # "Fail" row from a run that was never a verdict.
            print(f"                {report['outcome_rationale']}")
    print(f"  Checks:       {len(report['checks'])}")
    if args.target_base_url:
        print(f"  Iterations:   {report['checks']['client_observed_latency']['iterations']}")
        print("  Timing scope: client -> target -> controlled capture upstream -> target -> client")
    else:
        print(f"  Iterations:   {report['microbenchmarks']['iterations']}")
        print("  Timing scope: local in-process operations only")
    return 0 if report["passed"] else 1


OPERATOR_COMMANDS = (
    "assess",
    "audit-verify",
    "audit-checkpoint",
    "audit-checkpoint-verify",
    "compliance-report",
    "benchmark",
)

_BASE_INSTALL_HELP = """llm-shield-proxy: streaming privacy gateway and conformance harness

Subcommands (available on the base install):
  benchmark                 run the local or HTTP conformance profile
  assess                    offline aggregate-only pilot assessment
  audit-verify              verify a WORM audit log
  audit-checkpoint          checkpoint and sign closed audit chains
  audit-checkpoint-verify   verify a signed checkpoint
  compliance-report         build a compliance evidence pack

Run `llm-shield-proxy <subcommand> --help` for a subcommand's options.
`llm-shield-conformance` is the same conformance runner with no other subcommands.

Serving the proxy needs the optional gateway stack, which is NOT part of the base
install -- the harness must stay installable without it, so that measuring somebody
else's gateway does not require installing this one:

    pip install 'llm-shield-proxy[proxy]'
    llm-shield-proxy --host 0.0.0.0 --port 8000
"""


def main() -> int:
    """Console entry for `llm-shield-proxy`.

    Dispatches operator subcommands BEFORE importing the ASGI entry point, because
    `api/cli.py` imports uvicorn and the settings model at module scope and neither is
    in the base install. With the [proxy] extra present this delegates exactly as it
    always did, help text included.
    """
    argv = sys.argv[1:]
    if argv and argv[0] in OPERATOR_COMMANDS:
        return operator_main(argv)
    try:
        from llm_shield_proxy.api.cli import main as serve_main
    except ImportError:
        # No gateway stack. Explain rather than emit a traceback; `--help` still
        # answers, which is what a fresh install is most likely to be asked.
        if not argv or argv[0] in ("-h", "--help"):
            print(_BASE_INSTALL_HELP)
            return 0
        print(
            "This command needs the gateway stack, which the base install omits.\n"
            "Install it with:  pip install 'llm-shield-proxy[proxy]'",
            file=sys.stderr,
        )
        return 2
    serve_main()
    return 0


def conformance_main(argv: Optional[Sequence[str]] = None) -> int:
    """Console entry point for the conformance harness alone.

    Deliberately does NOT route through ``api/cli.py``, which imports uvicorn and the
    settings model at module scope before it dispatches a subcommand. This one runs on
    the base install -- stdlib plus httpx -- so a third party can measure their own
    gateway without installing this one.
    """
    return benchmark_main(argv)


def operator_main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if not arguments:
        print(
            "Expected a subcommand: assess, audit-verify, audit-checkpoint, "
            "audit-checkpoint-verify, benchmark, compliance-report",
            file=sys.stderr,
        )
        return 2
    command, command_args = arguments[0], arguments[1:]
    if command == "assess":
        return assess_main(command_args)
    if command == "audit-verify":
        return audit_verify_main(command_args)
    if command == "audit-checkpoint":
        return audit_checkpoint_main(command_args)
    if command == "audit-checkpoint-verify":
        return checkpoint_verify_main(command_args)
    if command == "compliance-report":
        return compliance_report_main(command_args)
    if command == "benchmark":
        return benchmark_main(command_args)
    print(f"Unknown operator subcommand: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    # `python -m llm_shield_proxy.cli <subcommand>` goes through the same dispatcher as
    # the console script, so the two cannot drift.
    sys.exit(main())
