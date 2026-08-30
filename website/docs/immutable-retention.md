# Immutable retention and multi-worker checkpoints

LLM-Shield-Proxy does not bundle a cloud storage SDK. The proxy writes signed per-worker chains, and an offline command produces a signed checkpoint that can be uploaded with the logs to the immutable store your organization already operates.

This keeps cloud clients and network I/O out of the proxy process.

## 1. Mount a stable signing key

Provision an Ed25519 private key through your existing secret manager and mount it read-only:

```dotenv
AUDIT_SIGNING_KEY_FILE=/run/secrets/llm-shield-audit-ed25519.pem
AUDIT_DURABILITY=required
AUDIT_DURABLE_PATH=/var/lib/llm-shield/audit-{instance_id}-{pid}.jsonl
```

`AUDIT_SIGNING_KEY_FILE` takes precedence over inline key material and fails startup when the file is missing or invalid. Keep the public key and its fingerprint in a separate evidence catalog. Rotation should start a new documented key epoch; do not overwrite the old public key.

## 2. Create one checkpoint for all workers

Use a separate checkpoint-signing key controlled by the evidence pipeline:

```bash
llm-shield-proxy audit-checkpoint \
  --audit-log audit-worker-101.jsonl \
  --audit-log audit-worker-102.jsonl \
  --audit-pubkey-file audit-public.pem \
  --signing-key-file checkpoint-private.pem \
  --out checkpoint-2026-08-30T1500Z.json

llm-shield-proxy audit-checkpoint-verify \
  --checkpoint checkpoint-2026-08-30T1500Z.json \
  --pubkey-file checkpoint-public.pem
```

The command verifies every event signature and chain transition before recording each worker's chain ID, terminal sequence, terminal hash, time range, event count, source checksum, and audit-key fingerprint. The manifest is signed and contains no prompt or matched protected value.

The chains remain independently ordered. The checkpoint does not claim a global event order. Use request IDs, trace IDs, or an external ordered event service when cross-worker ordering matters.

## 3. Retain the evidence outside the proxy trust domain

Upload the closed log segments, signed checkpoint, checkpoint public key, and a small metadata record identifying the retention policy. Verify the upload checksum, then apply or confirm the immutable policy.

Common operator-managed choices include:

- [Amazon S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html), using compliance mode when administrators must not be able to shorten retention;
- [Azure immutable Blob Storage](https://learn.microsoft.com/azure/storage/blobs/immutable-storage-overview), using a locked time-based policy or legal hold;
- [Google Cloud Storage Bucket Lock](https://cloud.google.com/storage/docs/bucket-lock), after testing the retention period before permanently locking it.

Do not copy a production retention command from documentation without approval. Compliance-mode and locked retention policies are intentionally difficult or impossible to reverse before expiry.

## 4. Recommended schedule

- Rotate or close worker log segments at a defined interval, such as hourly or daily.
- Generate a checkpoint only from closed segments.
- Upload logs and checkpoint to an identity-separated immutable account or project.
- Verify object checksums and retention status from a read-only audit identity.
- Alert if a worker is absent, a chain fails verification, an upload is late, or retention is not active.
- Keep checkpoint public keys and key-epoch metadata longer than the audit records they verify.

## What this establishes

The application verifies chain integrity and signs a multi-worker terminal-state manifest. The storage provider enforces retention. The operator controls identities, key lifecycle, upload completeness, monitoring, and policy duration. Together these controls can support an evidence-grade deployment; the Python package alone cannot certify one.
