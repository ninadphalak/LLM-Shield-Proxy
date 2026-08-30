# Tamper-Evident Audit Logging with Hash Chaining

[Back to Features Catalog](/docs/features-overview)

## What it does

Each audit event contains a SHA-256 link to the preceding event and is signed with Ed25519. The verifier detects edits, insertions, reordering, sequence gaps, malformed signatures, and unexpected signing-key fingerprints within the evidence it receives.

This is **tamper evidence**, not WORM storage by itself. A local append-only JSONL file can still be deleted or replaced by an administrator. To make a WORM retention claim, ship the file to storage whose immutability and retention controls are independently configured and tested (for example, an object-lock or compliance-mode archive).

## Delivery modes

| Mode | Request-path behavior | Persistence |
| :--- | :--- | :--- |
| `best_effort` (default) | Non-blocking; a full bounded queue can drop an event and increments `audit_events_dropped_total` | Structured stdout only unless an external collector persists it |
| `durable` | Waits for the audit worker to append and acknowledge each event; raises on timeout or sink failure | Local JSONL, flushed and `fsync`'d by default |
| `required` | Same persistence acknowledgement contract as `durable`; intended for deployments that treat audit failure as a request failure | Local JSONL, flushed and `fsync`'d by default |

`durable` and `required` require `AUDIT_DURABLE_PATH`. They trade request latency and availability for evidence completeness.

## Configuration

```dotenv
AUDIT_DURABILITY=durable
AUDIT_DURABLE_PATH=/var/lib/llm-shield/audit-{instance_id}-{pid}.jsonl
AUDIT_DURABLE_FSYNC=true
AUDIT_ENQUEUE_TIMEOUT_SECONDS=5
AUDIT_SIGNING_KEY_FILE=/run/secrets/llm-shield-audit-ed25519.pem
```

`AUDIT_SIGNING_KEY_FILE` is the preferred production source and fails startup if the mounted Ed25519 key is missing or invalid. Inline `AUDIT_SIGNING_PRIVATE_KEY` remains available for compatibility. Use a separate path per process. `{instance_id}` and `{pid}` are expanded automatically. A shared file across workers does not provide cross-process locking or a single global chain.

On restart, the proxy recovers the last valid record's `chain_id`, `sequence`, and hash from its configured file and emits `PROXY_RESUME`. Invalid or truncated final records fail initialization rather than silently starting a new chain.

## Offline verification

```bash
llm-shield-proxy audit-verify \
  --audit-log /var/lib/llm-shield/audit-instance-123.jsonl \
  --pubkey-file audit-public-key.pem \
  --json-out verification.json
```

The command exits non-zero when continuity or signature verification fails. Retain the trusted public key separately from the audit file.

## Security boundaries

- Hash chaining detects modification relative to adjacent records that are present. Deleting an unanchored suffix cannot be detected from the shortened file alone; periodically anchor the final hash and sequence in an independent system.
- Ed25519 authenticates records against the supplied public key. Key custody, rotation, and archival remain operator responsibilities.
- The event schema contains security metadata, not matched PII values or prompt bodies.
- `fsync` confirms an operating-system persistence request. It does not prove storage-device durability or regulatory retention.

For multiple workers, immutable retention, and separate terminal-state anchoring, follow the [checkpoint and retention guide](/docs/immutable-retention). The checkpoint aggregates verified terminal states but does not invent a global event order.

## Related tests

See `tests/test_audit_durability.py`, `tests/test_audit_signing.py`, and `tests/test_compliance_report.py`.
