# EU AI Act Compliance (Articles 12 & 14)

## Overview: High-Risk Systems & Oversight

The EU AI Act classifies certain Generative AI deployments as "high-risk," imposing stringent requirements around traceability, continuous monitoring, and human oversight. A core challenge for enterprise architects is achieving this traceability without violating concurrent data minimization mandates (The Article 12 Paradox).

The LLM-Shield-Proxy systematically addresses Articles 12 (Record-keeping) and 14 (Human oversight) through cryptographic attestation and hard systems-level containment.

## Satisfying Article 12: Record-Keeping and Traceability

Article 12 mandates that high-risk AI systems automatically record events ('logs') over their lifetime to ensure traceability of the system's functioning.

### WORM Audit Logging & Merkle Chaining
To provide absolute traceability without persisting raw user prompts to disk, the proxy implements **WORM (Write Once, Read Many) Audit Logging**. 
- **SHA-256 Sequential Merkle Hash Chaining:** Every redaction, tool-call interception, and configuration change generates a cryptographic event. These events are linked using sequential SHA-256 hashes, creating a Merkle chain. Any retroactive tampering with the logs will immediately invalidate the chain.
- **Proof of Non-Egress Receipt:** The proxy computes a rolling SHA-256 digest over the entire SSE stream. It emits an HMAC-signed attestation proof guaranteeing exactly what data was (and wasn't) sent to the external LLM provider, providing mathematical proof to EU auditors.

### NIST OSCAL Decision Traces
The **Universal Decision Trace Exporter** formats these cryptographic events into automated NIST OSCAL (SP 800-53 Rev. 5) assessment results and OpenTelemetry `gen_ai.*` spans. This allows seamless ingestion into GRC systems (Vanta, Drata) for continuous, provable record-keeping.

## Satisfying Article 14: Human Oversight and Agent Containment

Article 14 dictates that high-risk systems must be designed to allow effective human oversight to prevent or minimize risks to health, safety, or fundamental rights.

### Streaming Tool-Call RBAC
As AI agents become autonomous, the risk of unauthorized lateral movement (e.g., executing malicious SQL or shell commands) increases exponentially.
- **Mid-Stream Interception:** The proxy features pluggable streaming tool-call RBAC that intercepts JSON-RPC 2.0 / MCP (Model Context Protocol) function calls mid-stream.
- **Policy Evaluation:** Tool calls (like `exec_sql`) are synchronously evaluated against OPA (Open Policy Agent) and HashiCorp Vault resolvers utilizing atomic dictionaries and thundering-herd locks.

### Composite Agent Loop Circuit Breakers
To prevent runaway autonomous loops—a critical risk in agentic architectures—the proxy implements **Composite Agent Loop Circuit Breakers**. If an agent begins rapidly iterating or executing repetitive, unverified tool calls without human-in-the-loop validation, the circuit breaker halts the execution.

*(Reference the [Architecture & Cryptographic Data Flow](../../ARCHITECTURE.md) for deeper implementation details on the proxy's streaming capabilities).*
