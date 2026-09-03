# Tamper-Evident Audit Logging with Hash Chaining

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy links every audit event cryptographically to the previous event using a SHA-256 hash chain and signs them with an Ed25519 key. This provides **tamper evidence** (detecting edits, insertions, or reordering). It does not natively provide Write-Once-Read-Many (WORM) storage.

## How It Works
The proxy operates in different durability modes depending on your availability versus audit completeness requirements.

| Mode | Behavior | Persistence |
| :--- | :--- | :--- |
| `best_effort` (default) | Non-blocking. If the audit queue fills up, events are dropped to preserve proxy latency. | stdout only |
| `durable` | Blocks the request until the audit event is appended to disk. | Appended and `fsync`'d to local JSONL |
| `required` | Same as `durable`, but treats any audit failure (e.g., disk full) as an immediate HTTP 500 request failure. | Appended and `fsync`'d to local JSONL |

## Configuration Flags

```dotenv
AUDIT_DURABILITY=durable
AUDIT_DURABLE_PATH=/var/lib/llm-shield/audit-{instance_id}-{pid}.jsonl
AUDIT_DURABLE_FSYNC=true
AUDIT_SIGNING_KEY_FILE=/run/secrets/llm-shield-audit-ed25519.pem
```

## Implementation Details & Edge Cases
* **Process Separation:** Every worker process (`{pid}`) must maintain its own file. Mixing hash chains in a single file invalidates the continuity of all chains.
* **Restart Recovery:** On restart, the proxy reads the last record in the file to resume the hash chain. Corrupted tails will crash the proxy rather than silently starting a new chain.

## Verification
Use the bundled CLI to verify the chain offline:
```bash
llm-shield-proxy audit-verify \
  --audit-log /var/lib/llm-shield/audit-123.jsonl \
  --pubkey-file audit-public-key.pem
```
*If you omit the public key, the CLI will only verify the unkeyed hash chain, which provides no authenticity.*

## FAQ

**Q: If the file is hash-chained, is it WORM compliant?**
A: No. Hash chaining only proves tamper-evidence *if the records are present*. An attacker can delete the file or truncate the tail end of the log without breaking the math of the remaining records. You must ship these logs to true WORM storage (e.g., S3 Object Lock) to establish compliance.

## Practical Effect
This feature guarantees that any modification to a stored audit log file will be mathematically detected during verification, provided the public key is kept secure and the logs are shipped to immutable storage.

## Related Tests
Tests: `tests/test_audit_durability.py`, `tests/test_audit_signing.py`.
