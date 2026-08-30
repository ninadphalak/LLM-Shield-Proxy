# Compliance-Pack CLI Export

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
Proving compliance today typically means a compliance officer manually stitching together
OSCAL exports, audit log excerpts, and integrity checksums into something an external
auditor can actually review. **Compliance-Pack CLI Export** collapses that into one
command: `llm-shield-proxy compliance-report --framework=[hipaa|soc2|nist] --out=pack.zip`
bundles OSCAL assessment-results artifacts, a verified tamper-evident audit-log summary, and a
SHA-256 file-integrity manifest into a single `.zip`, with a generated Markdown summary an
auditor can read directly.

## How It Works
1. **Audit Evidence Verification:** Given `--audit-log`, the CLI re-walks the signed JSONL
   file, recomputing the SHA-256 hash chain and (given `--pubkey-file`, the PEM from
   [`GET /api/v1/audit/pubkey`](./ed25519-signed-audit-receipts.md)) verifying every
   Ed25519 signature - flagging any hash mismatch, chain discontinuity, or invalid
   signature as a chain break.
2. **OSCAL Summarization:** Given `--oscal-file` (e.g. from the
   [GRC Sidecar File Transport](./grc-webhook-sidecar-file-transport.md)), it counts
   assessment-result sets and observations; otherwise it generates a fresh OSCAL shell via
   the [`DecisionTraceExporter`](./universal-decision-trace-exporter.md).
3. **Integrity Manifest:** Every bundled artifact (OSCAL JSON, audit summary, chain-break
   report, and the source audit log itself) is SHA-256 checksummed into
   `checksums.sha256.json`.
4. **Markdown Narrative:** A framework-specific summary (HIPAA 45 CFR §164.312, SOC 2 CC6/
   CC7, or NIST SP 800-53 Rev. 5) is generated from the verified evidence and written as
   `SUMMARY.md` inside the pack.

```mermaid
flowchart TD
    A[Signed Hash-Chain Log] --> B[Verify Hash Chain + Ed25519 Signatures]
    C[OSCAL Artifact] --> D[Summarize Results & Observations]
    B --> E[SUMMARY.md + checksums.sha256.json]
    D --> E
    E --> F[compliance_pack.zip]
```

## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
  over the audit log (O(N) in event count) with no impact on the running proxy.
- **Overhead:** Zero - runs entirely outside the request path, typically as a scheduled
  job or on-demand before an audit.

## Configuration Flags
This is a CLI subcommand, not a runtime engine flag:

| Flag | Description |
| :--- | :--- |
| `--framework` | `hipaa`, `soc2`, or `nist` - selects the Markdown narrative and regulatory reference. |
| `--out` | Output `.zip` path (default `./compliance_pack.zip`). |
| `--audit-log` | Path to a signed hash-chain audit JSONL file. Omit to skip audit evidence. |
| `--oscal-file` | Path to a persisted OSCAL JSON/JSONL artifact. Omit to generate an empty OSCAL shell. |
| `--pubkey-file` | PEM file with the proxy's Ed25519 audit public key, to verify receipt signatures. |

## Critical Logic & Edge Cases
* **Missing Evidence Is Not an Error:** Omitting `--audit-log` or `--oscal-file` produces a
  pack with `chain_valid: null` / an empty OSCAL shell rather than failing - useful for
  smoke-testing the pack format before wiring up log capture.
* **Non-Zero Exit on Tamper:** The CLI exits `1` when hash-chain verification fails,
  so it can gate a CI job or scheduled compliance run rather than silently produce a pack
  that documents its own tampering.
* **Signature Verification Is Opt-In:** Without `--pubkey-file`, the hash chain is still
  verified, but Ed25519 signatures are reported as unchecked rather than failing the run -
  the public key must be distributed out-of-band from the log file itself.

## FAQ

**Q: Does this require the proxy to be running?**
A: No. It operates entirely on files you already have - a captured signed audit log and an
optional OSCAL artifact - so it can run as a standalone step in a compliance pipeline.

**Q: Can I automate this for recurring audits?**
A: Yes - since it's a plain CLI with a non-zero exit code on integrity failure, it fits
directly into a cron job or CI pipeline that archives the resulting `.zip` on a schedule.

## Plainspeak
This is the "generate the audit binder" button. Instead of a compliance officer manually
copy-pasting log excerpts and OSCAL JSON into a folder before an audit, one command
verifies the evidence is intact and hands you a `.zip` you can email to an auditor.

## Related Tests
See [`tests/test_compliance_report.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_compliance_report.py) for reference implementations and tamper-detection edge cases.
