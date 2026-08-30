# Audit evidence plane: implementation status

**Status:** the lightweight application-side evidence plane is implemented. Immutable retention and managed key custody are infrastructure controls.

The repository now implements the core evidence mechanics needed for an enterprise pilot. It would be inaccurate to describe the default installation as durable WORM or a complete evidence-grade compliance plane.

## Implemented in the project

- privacy-safe structured audit metadata;
- SHA-256 predecessor links, stable chain IDs, and monotonic sequence numbers;
- Ed25519 signatures and public-key fingerprints;
- offline verification of hashes, signatures, ordering, gaps, and key mismatch;
- a tamper negative control in the public conformance harness;
- default bounded `best_effort` delivery with drop metrics;
- opt-in `durable` and `required` local JSONL modes with acknowledgement, flush/`fsync`, and restart recovery;
- OSCAL 1.2 assessment-result export and integrity-manifested compliance packs.
- fail-closed loading of a secret-manager-mounted signing key;
- signed terminal-state checkpoints that aggregate independently ordered worker chains;
- offline checkpoint signature and fingerprint verification.

## Not supplied by the local proxy

| Required production property | Why the current process cannot establish it | Deployment or roadmap action |
| :--- | :--- | :--- |
| Immutable WORM retention | A local administrator can delete or replace a JSONL file | Export to independently configured object-lock/compliance-mode storage and test retention policy |
| Deleted-suffix detection | A shortened internally valid chain has no terminal reference | Periodically anchor final chain ID, sequence, and hash in an independent trust domain |
| Production key custody | The default key is ephemeral and self-asserted | Mount a stable key from the operator's secret manager and archive its public key |
| Global event ordering | Worker chains are independent | The checkpoint aggregates terminal states but deliberately does not invent a global event order; use request IDs or an external event system when global ordering is required |
| Complete event capture | `best_effort` may drop under pressure | Select `durable`/`required`, alert on failures, and test failure semantics |
| Regulatory attestation | Software artifacts are not an auditor opinion | Operate controls, collect evidence over the audit period, and obtain independent assessment |

## Recommended pilot profile

Use `AUDIT_SIGNING_KEY_FILE`, a unique durable path per process, `AUDIT_DURABILITY=required` when audit loss must fail the request, an external immutable sink, and a separately retained checkpoint. Exercise disk-full, permission, restart, truncation, wrong-key, tampering, and queue-pressure cases before production.

The [immutable retention and checkpoint guide](/docs/immutable-retention) provides the no-SDK workflow. The [audit contract](/docs/features/enterprise-auditing-compliance/worm-compliant-audit-logging-with-hash-chaining) contains the runtime configuration details. The [Open Conformance Lab](/docs/conformance) tests verifier behavior but does not validate your storage or key-management controls.
