"""Compliance & Administrative CLI for LLM-Shield-Proxy.

Provides operator-facing subcommands that do not launch the ASGI server, such
as bundling auditor-ready compliance packs from WORM audit evidence and OSCAL
assessment artifacts.
"""

from __future__ import annotations

import argparse
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
    parser.add_argument("--pubkey-file", default=None, help="Optional Ed25519 public-key PEM path.")
    parser.add_argument("--json-out", default=None, help="Optional path for the machine-readable verification summary.")
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
    print(f"Integrity issues:   {len(result['chain_breaks'])}")
    if result["signature_checked"]:
        print(f"Valid signatures:   {result['signatures_valid']}")
        print(f"Invalid signatures: {result['signatures_invalid']}")
    return 0 if result["chain_valid"] is True else 1


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
        description="Run the local streaming privacy conformance and microbenchmark harness.",
    )
    parser.add_argument("--iterations", type=int, default=2_000, help="Measured iterations (default: %(default)s).")
    parser.add_argument("--json-out", default="./CONFORMANCE_LATEST.json", help="Machine-readable result path.")
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

    try:
        report = run_conformance(args.iterations)
        destination = write_conformance_report(report, args.json_out)
    except (OSError, ValueError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 2

    print(f"Conformance report written to {destination}")
    print(f"  Passed:       {report['passed']}")
    print(f"  Checks:       {len(report['checks'])}")
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
