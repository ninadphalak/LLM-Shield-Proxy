# EU AI Act Compliance (Articles 12 & 14)

## Overview: High-Risk Systems & Oversight

The EU AI Act classifies certain Generative AI deployments as "high-risk," imposing stringent requirements around traceability, continuous monitoring, and human oversight. A core challenge for enterprise architects is achieving this traceability without violating concurrent data minimization mandates (The Article 12 Paradox).

The LLM-Shield-Proxy systematically addresses Articles 12 (Record-keeping) and 14 (Human oversight) through cryptographic attestation and hard systems-level containment.

## Satisfying Article 12: Record-Keeping and Traceability

Article 12 mandates that high-risk AI systems automatically record events ('logs') over their lifetime to ensure traceability of the system's functioning.

### Tamper-Evident Audit Chaining
The proxy can generate privacy-safe audit metadata linked with sequential SHA-256 hashes and signed with Ed25519. Verification detects modification or sequence gaps in the records received. Durable local delivery is opt-in; WORM retention requires a separately configured immutable store and operating controls.
- **Stream attestation receipt:** The proxy can compute a rolling SHA-256 digest over an SSE stream and emit an HMAC-signed receipt. It establishes integrity for the observed stream under the configured key; it is not independent proof of every upstream system's behavior.

### NIST OSCAL Decision Traces
The **Universal Decision Trace Exporter** formats these cryptographic events into automated NIST OSCAL (SP 800-53 Rev. 5) assessment results and OpenTelemetry `gen_ai.*` spans. This allows seamless ingestion into GRC systems (Vanta, Drata) for continuous, provable record-keeping.

## Satisfying Article 14: Human Oversight and Agent Containment

Article 14 dictates that high-risk systems must be designed to allow effective human oversight to prevent or minimize risks to health, safety, or fundamental rights.

### Streaming Tool-Call RBAC
As AI agents become autonomous, the risk of unauthorized lateral movement (e.g., executing malicious SQL or shell commands) increases exponentially.
- **Mid-Stream Interception:** The proxy features pluggable streaming tool-call RBAC that intercepts JSON-RPC 2.0 / MCP (Model Context Protocol) function calls mid-stream.
- **Policy Evaluation:** Tool calls (like `exec_sql`) are synchronously evaluated against OPA (Open Policy Agent) and HashiCorp Vault resolvers utilizing atomic dictionaries and thundering-herd locks.

### Composite Agent Loop Circuit Breakers
To prevent runaway autonomous loops-a critical risk in agentic architectures-the proxy implements **Composite Agent Loop Circuit Breakers**. If an agent begins rapidly iterating or executing repetitive, unverified tool calls without human-in-the-loop validation, the circuit breaker halts the execution.

*(Reference the [Architecture & Cryptographic Data Flow](/docs/architecture) for deeper implementation details on the proxy's streaming capabilities).*
