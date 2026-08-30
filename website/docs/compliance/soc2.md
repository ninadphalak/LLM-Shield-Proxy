# SOC 2 Type II Compliance

## Overview: Trust Services Criteria for GenAI

Service Organization Control 2 (SOC 2) Type II compliance evaluates the operating effectiveness of a system's security, availability, processing integrity, confidentiality, and privacy controls over a prolonged period.

For AI gateways, standard Trust Services Criteria (TSC) such as Logical Access (CC6.1), Boundary Protection (CC6.6), and System Operations/Anomaly Detection (CC7.2) require advanced cryptographic and systemic guardrails.

## Logical Access & Role-Based Access Control (CC6.1)

To ensure that only authorized entities (both human and autonomous agents) can execute sensitive operations, the proxy implements rigid access frameworks.

### Pluggable Streaming Tool-Call RBAC
As LLM agents utilize protocols like MCP (Model Context Protocol) to execute downstream functions, standard API keys are insufficient.
- The proxy intercepts JSON-RPC 2.0 and MCP function calls (e.g., `exec_sql`, `shell_exec`) mid-stream.
- Access requests are validated against **OPA (Open Policy Agent)** and **HashiCorp Vault** resolvers utilizing atomic dictionaries and thundering-herd locks.
- **Tenant-Scoped Virtual Key RBAC:** Multi-tenant environments are segregated logically. Virtual keys are scoped per tenant, ensuring strict boundary enforcement between organizational units or distinct AI agent personas.

## Boundary Protection & System Operations (CC6.6 & CC7.2)

### Tamper-Evident SHA-256 Hash Chains
Audit records can be linked with SHA-256 and signed with Ed25519 so offline verification detects changes, gaps, and signing-key mismatch in the evidence received. This supports control testing; it does not establish SOC 2 compliance by itself. Configure durable delivery for completeness and an independent immutable store when WORM retention is required.

### RFC 6902 Differential Logs
To maintain confidentiality while logging:
- The proxy utilizes **RFC 6902 JSON patch differential audit logging**. Instead of logging the raw request (which could contain sensitive enterprise IP), the system logs the structural *delta*-recording exactly what rules were triggered and what entity categories were redacted.

### Insider Leak Forensics & Anomaly Detection
To satisfy anomaly detection and prevent data exfiltration (CC7.2):
- **Steganographic Canary Watermarking:** The proxy utilizes dynamic zero-width Unicode steganographic canary watermarking. This allows enterprise forensics teams to trace leaked model outputs back to specific users, agents, or sessions without altering the visible text of the LLM response.
- **Composite Agent Loop Circuit Breakers:** To protect system availability against runaway autonomous AI loops, circuit breakers monitor execution patterns and halt execution if threshold limits (e.g., rapidly repeating unauthorized SQL queries) are breached.

*(Reference the [Architecture & Cryptographic Data Flow](/docs/architecture) for deeper implementation details).*
