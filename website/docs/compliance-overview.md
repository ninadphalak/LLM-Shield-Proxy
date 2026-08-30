# Compliance controls and evidence boundaries

LLM-Shield-Proxy combines in-VPC inspection, upstream-boundary tests, privacy-safe audit metadata, SHA-256 predecessor links, Ed25519 signatures, and OSCAL 1.2 assessment artifacts. These features can **support** technical controls and audit evidence. They do not make a deployment or organization compliant, replace legal advice, or constitute certification.

## What the evidence can establish

| Evidence | What it supports | What it does not establish |
| :--- | :--- | :--- |
| Configured-upstream conformance test | Known protected fixtures are absent from serialized transformed requests in the tested configuration | Universal detector recall, all production traffic, or network-path isolation |
| Hash chain + Ed25519 signatures | Changes, gaps, ordering errors, and key mismatch within evidence supplied to the verifier | Storage-level WORM, complete event capture, trusted key custody, or detection of an unanchored deleted suffix |
| Durable local JSONL mode | Append acknowledgement and restart recovery for one configured process/file | Multi-worker global ordering or immutable retention |
| OSCAL 1.2 assessment results | Machine-readable control observations and evidence exchange | Control effectiveness, authorization to operate, or framework certification |
| Compliance evidence pack | Integrity-manifested bundle of supplied evidence and generated summaries | Independent auditor attestation |

## Framework mappings

- [SOC 2](/docs/compliance/soc2): supports access-control, boundary-protection, logging, and monitoring evidence selected by the operator and auditor.
- [HIPAA](/docs/compliance/hipaa): supports technical safeguards such as access control, integrity checks, and transmission-security design. A covered entity or business associate remains responsible for its risk analysis and safeguards.
- [GDPR](/docs/compliance/gdpr): supports data-minimization and privacy-by-design engineering. Lawful basis, notices, rights handling, retention, and processor/controller duties remain organizational responsibilities.
- [EU AI Act](/docs/compliance/eu_ai_act): supports event metadata and human-enforced tool policy. System classification and provider/deployer obligations require separate analysis.
- [NIST, ISO, and cryptographic controls](/docs/compliance/nist_iso_fips): exports mappings and performs implementation self-tests; it does not claim a FIPS-validated cryptographic module or NIST/ISO certification.

## Audit delivery truth boundary

The default `AUDIT_DURABILITY=best_effort` path is non-blocking and process-local. It can drop events when the bounded queue is full, starts a new chain after restart, and uses an ephemeral Ed25519 key unless a stable signing key is configured.

`durable` and `required` modes add acknowledged local JSONL append, `fsync` by default, and chain recovery after restart. Local files remain deletable or replaceable by administrators. WORM retention requires an independently configured immutable store; suffix-deletion detection requires an external terminal-hash/sequence anchor; production authenticity requires controlled key generation, custody, rotation, and archival.

## How to present the project accurately

Use: "supports SOC 2 or HIPAA technical controls and produces verifiable evidence artifacts."

Do not use: "SOC 2 compliant," "HIPAA compliant," "guaranteed non-egress," or "WORM audit log" without the deployment controls and independent validation those claims require.

Run the [Open Streaming-Privacy Conformance Lab](/docs/conformance) and attach its JSON report to a pilot assessment. Performance figures are environment-scoped and must identify the measured operation and exclusions.
