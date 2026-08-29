"""Compliance & Administrative CLI for LLM-Shield-Proxy.

Provides operator-facing subcommands that do not launch the ASGI server, such
as bundling auditor-ready compliance packs from WORM audit evidence and OSCAL
assessment artifacts.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from llm_shield_proxy.compliance.report import SUPPORTED_FRAMEWORKS


def build_compliance_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy compliance-report",
        description="Generate an auditor-ready compliance pack (OSCAL + WORM audit summary + checksums).",
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
        help="Path to a WORM audit log JSONL file (captured proxy stdout). Omit to skip audit evidence.",
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


def main() -> None:
    sys.exit(compliance_report_main())


if __name__ == "__main__":
    main()
