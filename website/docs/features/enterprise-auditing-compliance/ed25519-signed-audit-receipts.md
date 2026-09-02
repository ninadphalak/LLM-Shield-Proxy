# Ed25519-Signed Audit Receipts

[Back to Features Catalog](/docs/features-overview)

## What it does

The audit logger signs canonical hash-chained records with Ed25519 on instrumented paths. A
verifier with a separately trusted public key can detect changes within the supplied chain. It
cannot detect deletion of an unanchored suffix.

Signing does not make the storage immutable and does not, by itself, establish legal non-repudiation. Those outcomes depend on key custody, identity binding, retention controls, and operating procedures.

## Key provisioning

Set `AUDIT_SIGNING_PRIVATE_KEY` to a PEM private key or a 32-byte seed encoded as base64 or hex. If it is unset, the proxy generates an ephemeral key for development. An ephemeral key changes at restart and is unsuitable for long-lived evidence verification.

The public key and SHA-256 fingerprint are available at `GET /api/v1/audit/pubkey`. Archive the trusted key or fingerprint outside the audit sink.

## Receipt fields

Each completed record includes:

- `chain_id` and monotonically increasing `sequence`
- `previous_hash` and `hash`
- base64 `signature`
- `public_key_fingerprint`

Use the bundled verifier rather than reconstructing canonical serialization manually:

```bash
llm-shield-proxy audit-verify --audit-log audit.jsonl --pubkey-file audit-public-key.pem
```

`--pubkey-file` is what makes the result meaningful. Without it the verifier can only
confirm that an unkeyed hash chain is self-consistent, which a forger reproduces at will,
so the command exits non-zero unless `--allow-unsigned` is passed. When a key is supplied,
any record lacking a signature fails the whole file rather than being counted as merely
unsigned.

## Performance and failure behavior

In the default `best_effort` mode, hashing and signing run on the audit worker and the request does not wait for persistence. In `durable` or `required` mode, the caller waits for an acknowledged append; a sink failure or timeout is surfaced. Measure this trade-off on the storage used in production rather than relying on a generic signing-latency claim.

See [Tamper-Evident Audit Logging with Hash Chaining](./worm-compliant-audit-logging-with-hash-chaining.md) for durability configuration and WORM boundaries.

## Related tests

See `tests/test_audit_signing.py` and `tests/test_audit_durability.py`.
