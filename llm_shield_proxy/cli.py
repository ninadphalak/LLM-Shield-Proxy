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

from llm_shield_proxy.compliance.report import SUPPORTED_FRAMEWORKS


def build_compliance_report_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--capture-host", default="127.0.0.1", help="Controlled upstream bind host.")
    parser.add_argument("--capture-port", type=int, default=8765, help="Controlled upstream bind port.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def benchmark_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_benchmark_parser().parse_args(argv)

    # The conformance harness is an offline test tool. Disable only configured
    # OpenTelemetry export before importing PIIEngine/tracing; do not alter the
    # product's anonymous usage tracking setting.
    from llm_shield_proxy.core.config import settings

    settings.TELEMETRY_ENABLED = False
    settings.TELEMETRY_ENDPOINT_URL = None

    from llm_shield_proxy.conformance import run_conformance, write_conformance_report

    http_profile = bool(args.target_base_url)
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
                extra_headers=headers,
            )
        else:
            report = run_conformance(iterations)
        destination = write_conformance_report(report, json_out)
    except (OSError, ValueError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2

    print(f"Conformance report written to {destination}")
    print(f"  Passed:       {report['passed']}")
    print(f"  Checks:       {len(report['checks'])}")
    if args.target_base_url:
        print(f"  Iterations:   {report['checks']['client_observed_latency']['iterations']}")
        print("  Timing scope: client -> target -> controlled capture upstream -> target -> client")
    else:
        print(f"  Iterations:   {report['microbenchmarks']['iterations']}")
        print("  Timing scope: local in-process operations only")
    return 0 if report["passed"] else 1


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


def main() -> None:
    sys.exit(operator_main())


if __name__ == "__main__":
    main()
