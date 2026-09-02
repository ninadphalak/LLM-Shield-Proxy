# Tamper-Evident Audit Logging with Hash Chaining

[Back to Features Catalog](/docs/features-overview)

## What it does

Each audit event contains a SHA-256 link to the previous event and an Ed25519 signature. For the
records it receives, the verifier checks edits, insertions, ordering, sequence gaps, signatures,
and signing-key fingerprints.

This provides **tamper evidence**, not WORM storage. An administrator can still delete or replace
the local JSONL file. A WORM claim requires a separately configured and tested immutable-retention
system, such as object-lock or compliance-mode storage.

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

**Exit 0 requires signature verification.** The record hash is an unkeyed SHA-256, so
anyone can compute a self-consistent chain over records they wrote themselves; continuity
alone is not evidence of authenticity. Running without `--pubkey-file` therefore exits
non-zero and reports `Authenticity: NOT VERIFIED`. Pass `--allow-unsigned` to accept a
consistency-only result deliberately.

The output also prints the terminal sequence number and terminal hash. Record them
externally: comparing them against an independently held anchor is the only way to detect
a deleted suffix.

An empty or all-blank audit file is reported as invalid rather than as a clean zero-event
chain, and a file carrying more than one `chain_id` is rejected — one file is one worker's
chain, and mixing chains removes every record's predecessor.

## Security boundaries

- Hash chaining detects modification relative to adjacent records that are present. Deleting an unanchored suffix cannot be detected from the shortened file alone; periodically anchor the final hash and sequence in an independent system.
- A record's declared `initial_hash` is chosen by whoever wrote the record. It marks a well-formed chain start; it is not evidence of origin. Authenticity comes only from the Ed25519 signature.
- Ed25519 authenticates records against the supplied public key. Key custody, rotation, and archival remain operator responsibilities.
- The event schema contains security metadata, not matched PII values or prompt bodies.
- `fsync` confirms an operating-system persistence request. It does not prove storage-device durability or regulatory retention.

For multiple workers, immutable retention, and separate terminal-state anchoring, follow the [checkpoint and retention guide](/docs/immutable-retention). The checkpoint aggregates verified terminal states but does not invent a global event order.

## Related tests

See `tests/test_audit_durability.py`, `tests/test_audit_signing.py`, and `tests/test_compliance_report.py`.
