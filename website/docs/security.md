[⬅️ Back to README](/)

# Security Threat Model and Controls

## OWASP Top 10 for LLMs Mapping

| OWASP Threat | LLM-Shield-Proxy Mitigation |
| :--- | :--- |
| **LLM01: Prompt Injection** | Pattern neutralization for selected instruction-like text in tool content. Not a general prompt-injection defense. |
| **LLM02: Insecure Output Handling** | Limits JSON/SSE parsing depth. Sanitizes markdown image URLs to prevent exfiltration. |
| **LLM04: Model Denial of Service** | Buffer, recursion, and payload size limits mitigate basic resource exhaustion attacks. |
| **LLM06: Sensitive Information Disclosure** | 3-tier detection cascade redacts recognized PII before egress. |
| **LLM07: Insecure Plugin Design** | Enforces JWT/DPoP authentication and RBAC policies on tool calls. |
| **LLM08: Excessive Agency** | MCP tool allowlist policies. Agent loop circuit breaker stops repetitive polling. |

## 22-Vector Threat Matrix Verification

The project includes automated adversarial testing for specific threat vectors.

| Threat Vector | Proxy Defense Mechanism |
| :--- | :--- |
| **Streaming Packet Splitting** | Sliding-window prefix-overlap buffer holds incomplete tokens. |
| **Early Stream Termination** | Deterministic buffer flush on upstream disconnect. |
| **Unicode Smuggling** | Strips zero-width characters and normalizes Unicode. |
| **BiDi Evasion** | Strips Right-to-Left Overrides before regex matching. |
| **Base64 Obfuscation** | Skips inspection inside long Base64 payloads to prevent ReDoS, while preserving boundary detection. |
| **Markdown Image Exfiltration** | Sanitizes outbound query parameters in markdown image URLs. |
| **JSON Recursion Bomb** | Strict parsing depth limit (`max_depth=20`). |
| **Slowloris Memory Exhaustion** | 64KB buffer limit and 1MB SSE line limit. |
| **SSRF / DNS Rebinding** | Resolves all domains in tool calls and rejects loopback, link-local, and cloud metadata IPs. |
| **Audit Log Tampering** | SHA-256 hash chaining and Ed25519 digital signatures. |
| **DPoP Proof Replay** | RFC 9449 `jti` replay cache rejects reused cryptographic proofs. |

## Vulnerability Reporting

The project publishes security fixes for the latest release line only. 

If you discover a security vulnerability, **do not open a public GitHub issue**.

Report the issue confidentially to:
- **Contact:** Ninad Phalak
- **Email:** `ninad.phalak@gmail.com`

Please include reproduction steps and an impact assessment. We aim to respond within 48 hours.
