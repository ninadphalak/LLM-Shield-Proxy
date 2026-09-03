# Ed25519-Signed Audit Receipts

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy signs canonical, hash-chained audit records using the Ed25519 cryptographic algorithm. An auditor with the correct public key can detect if records in the chain have been modified or reordered. It does not prevent an attacker from deleting the entire unanchored tail (suffix) of the log.

## How It Works
Signing records detects tampering within the file but relies on secure key custody and log retention infrastructure to provide true non-repudiation.

1. **Key Provisioning:** Operators provide a private key (`AUDIT_SIGNING_PRIVATE_KEY`). If omitted, the proxy generates an ephemeral key for development, which changes on restart (and is therefore useless for long-term audits).
2. **Public Key Distribution:** The corresponding public key is exposed at `GET /api/v1/audit/pubkey`.
3. **Receipt Generation:** Each emitted audit record includes the `chain_id`, monotonically increasing `sequence`, `previous_hash`, the new `hash`, a base64 `signature`, and the `public_key_fingerprint`.

## Performance Profile
- **Overhead:** Hashing and Ed25519 signing consume CPU resources. In `best_effort` mode, this occurs asynchronously. In `durable` or `required` modes, the caller blocks while waiting for the signature and persistence. 

## Configuration Flags

| Environment Variable | Description |
| :--- | :--- |
| `AUDIT_SIGNING_PRIVATE_KEY` | A PEM private key or a 32-byte seed encoded as base64 or hex. |

## Implementation Details & Edge Cases
* **CLI Verification:** Verify the hash chain and signatures using the bundled CLI:
  ```bash
  llm-shield-proxy audit-verify --audit-log audit.jsonl --pubkey-file audit-public-key.pem
  ```
  Without `--pubkey-file`, the CLI only validates the self-consistent hash chain, which an attacker can trivially forge.

## FAQ

**Q: Does signing the logs make them immutable?**
A: No. Cryptographic signatures make *tampering evident*, but they do not make storage *immutable*. You must configure your storage layer (e.g., AWS S3 Object Lock) to prevent deletion. See [Tamper-Evident Audit Logging with Hash Chaining](./worm-compliant-audit-logging-with-hash-chaining.md).

## Practical Effect
This feature cryptographically signs audit log entries so modifications can be detected by the CLI verifier. It depends entirely on strict private key security and external WORM (Write Once, Read Many) storage to provide legal non-repudiation.

## Related Tests
Tests: `tests/test_audit_signing.py` and `tests/test_audit_durability.py`.
