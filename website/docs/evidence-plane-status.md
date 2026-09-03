# Audit Evidence Plane Status

The application-side evidence generation is implemented, but deploying a complete compliance solution requires external infrastructure controls.

## Implemented in the Proxy
- Privacy-safe structured audit metadata generation.
- SHA-256 predecessor links for tamper-evidence.
- Ed25519 digital signatures.
- Offline verification tooling for hashes, signatures, and ordering.
- OSCAL 1.2 assessment-result exports.
- `durable` and `required` local JSONL append-only modes with restart recovery.
- Signed terminal-state checkpoints to aggregate multiple worker chains.

## Required External Infrastructure
The proxy itself cannot guarantee the following properties. You must configure external systems to achieve them:

| Requirement | Implementation Action |
| :--- | :--- |
| **Immutable WORM Retention** | Export proxy JSONL logs to AWS S3 Object Lock, Azure Blob Immutable Storage, etc. A local admin can always delete the proxy's local file. |
| **Complete Event Capture** | If using `best_effort` durability, the proxy may drop audit events under high load. Use `required` mode and monitor your disk I/O. |
| **Key Custody** | Mount a stable Ed25519 key from a secret manager (like AWS KMS or HashiCorp Vault) rather than relying on auto-generated ephemeral keys. |
| **Global Event Ordering** | Proxy worker chains are independent. Rely on injected request IDs or distributed tracing to order events globally across multiple workers. |

## Recommended Pilot Profile
For a production-grade pilot, configure:
1. `AUDIT_SIGNING_KEY_FILE` mounted from a secret manager.
2. `AUDIT_DURABILITY=required` so audit failures fail the request.
3. A sidecar (e.g., Fluent Bit) to ship the JSONL files to an immutable object store.
4. Scheduled execution of the `audit-checkpoint` CLI to seal worker chains.
