# Immutable Retention and Checkpoints

LLM-Shield-Proxy does not contain built-in integrations for cloud storage providers (like AWS S3 or Google Cloud Storage). To achieve WORM (Write-Once, Read-Many) compliance for your audit logs, you must export the proxy's local JSONL logs to an external immutable storage system.

## 1. Mount a Stable Signing Key
By default, the proxy generates an ephemeral Ed25519 key on startup. For production audit logs, you must mount a stable private key from your secret manager:

```dotenv
AUDIT_SIGNING_KEY_FILE=/run/secrets/llm-shield-audit-ed25519.pem
AUDIT_DURABILITY=required
AUDIT_DURABLE_PATH=/var/lib/llm-shield/audit-{instance_id}-{pid}.jsonl
```

## 2. Generate Checkpoints
Each proxy worker process maintains its own independent hash chain. When you rotate log files, you use the offline `audit-checkpoint` CLI to verify the chains and generate a signed terminal-state manifest (checkpoint) covering all workers.

```bash
llm-shield-proxy audit-checkpoint \
  --audit-log audit-worker-101.jsonl \
  --audit-log audit-worker-102.jsonl \
  --audit-pubkey-file audit-public.pem \
  --signing-key-file checkpoint-private.pem \
  --out checkpoint-2026-08-30.json
```

## 3. Retain Evidence Externally
Configure a log forwarding agent (e.g., Fluent Bit, Datadog Agent) to upload the rotated JSONL files and the generated checkpoint JSON to immutable storage.

Common implementations:
- **Amazon S3 Object Lock** (Compliance Mode)
- **Azure Immutable Blob Storage** (Time-based retention policies)
- **Google Cloud Storage Bucket Lock**

*Warning: Ensure you thoroughly test your retention policies in a staging environment. Immutable storage policies are often impossible to reverse or delete before the retention period expires.*

## Recommended Operational Schedule
1. **Rotate:** Close worker log segments periodically (e.g., hourly or daily).
2. **Checkpoint:** Generate a single checkpoint encompassing all closed segments.
3. **Upload:** Ship the logs and checkpoint to immutable storage.
4. **Verify:** Periodically run the offline `audit-checkpoint-verify` CLI against the stored logs to ensure integrity.
