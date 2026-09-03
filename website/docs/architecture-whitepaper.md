# Streaming-Privacy Gateway Architecture Whitepaper

## Abstract
LLM-Shield-Proxy is an open-source, self-hosted proxy for inspecting and securing LLM (Language Model) and MCP (Model Context Protocol) traffic. Operating within your VPC, it enforces data privacy and security boundaries before traffic leaves your network. It features zero-egress PII redaction, streaming (SSE) fragmentation safety, dynamic tool policy enforcement, and tamper-evident audit logging. 

## 1. Boundary and Threat Model
The core security boundary is the serialized request dispatched to the upstream LLM provider. The proxy guarantees **zero egress** of detected PII, meaning unredacted sensitive values will not cross this boundary. 

Key threat vectors mitigated:
- **Fragmentation:** PII split across streaming SSE chunks or input tokens.
- **SSRF:** Prompt-injected tool calls attempting to reach internal networks.
- **Audit Tampering:** Covert modification or deletion of security logs.

## 2. Streaming Data Plane
### 2.1 Inbound Transformation
Traffic passes through a 3-Tier detection cascade:
1. **Google RE2:** Fast, deterministic regex for structured identifiers (SSN, emails).
2. **Shannon Entropy:** Heuristic scanning for unstructured secrets (API keys).
3. **ONNX NER:** Local, quantized NLP for conversational entities.

### 2.2 Fragment-Safe SSE Rehydration
Streaming responses often split replaced tokens across multiple Server-Sent Event (SSE) chunks. The `SSERehydrationBuffer` holds a precise lookahead buffer to reconstruct these fragmented tokens before sending them to the client, preserving both the data and the SSE protocol framing.

## 3. MCP Governance Plane
The proxy implements a streaming JSON parser that extracts tool execution keys (`name` or `method`) from MCP requests. These are evaluated against a Role-Based Access Control (RBAC) policy. Unauthorized tool calls are blocked at the proxy layer, preventing the LLM from executing them.

## 4. Audit and Compliance Evidence
The proxy emits verifiable audit records:
- **Hash Chaining:** Every event contains the SHA-256 hash of the previous event.
- **Digital Signatures:** Events are signed using an Ed25519 private key.
- **OSCAL Artifacts:** Emits NIST OSCAL 1.2 assessment results.

*Note: While these records are tamper-evident, achieving full WORM (Write Once, Read Many) compliance requires shipping these logs to an immutable storage backend (e.g., AWS S3 Object Lock).*

## 5. Conclusion
LLM-Shield-Proxy provides verifiable streaming privacy and governance. By centralizing security rules, redaction, and audit logging into a single in-VPC gateway, it allows organizations to safely adopt external LLM APIs.
