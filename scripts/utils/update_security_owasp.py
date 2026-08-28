import re

with open("SECURITY.md", "r", encoding="utf-8") as f:
    text = f.read()

# Insert the OWASP mapping right before the Threat Matrix table
owasp_mapping = """
## 🛡️ OWASP Top 10 for LLMs (v1.1) Mapping

LLM-Shield-Proxy provides comprehensive mitigation for **8 out of the 10** critical vulnerabilities identified by OWASP for Large Language Model applications. (Training Data Poisoning and Supply Chain Vulnerabilities are handled at the model deployment layer).

| OWASP Threat | LLM-Shield-Proxy Mitigation |
| :--- | :--- |
| **LLM01: Prompt Injection** | Mitigated by `INDIRECT_PROMPT_INJECTION_PATTERN` and AST-Aware Semantic Firewall. |
| **LLM02: Insecure Output Handling** | Mitigated by JSON Recursion Bomb Defense and XSS/Markdown Exfiltration blockers. |
| **LLM04: Model Denial of Service** | Mitigated by Slowloris Buffer limits, `64KB` Backpressure Guards, and `max_tokens` limits. |
| **LLM05: Supply Chain Vulnerabilities** | Addressed by WORM-Compliant Merkle Attestation for all outbound requests, proving un-tampered egress. |
| **LLM06: Sensitive Information Disclosure** | Mitigated by 3-Tier Redaction Cascade (DFA Regex, Shannon Entropy, ONNX NER) and Stateless Crypto. |
| **LLM07: Insecure Plugin Design** | Mitigated by the Edge-Level Agent Identity Enforcer (JWT/DPoP) and Autonomous Agent Circuit Breakers. |
| **LLM08: Excessive Agency** | Mitigated by Granular Entity Policy Scopes (Role-Based Access Controls) restricting tool calls deterministically. |
| **LLM10: Model Theft** | Mitigated by Dynamic Canary Watermarking & Steganography to track stolen outputs back to the source. |

## 18-Vector Threat Matrix"""

text = re.sub(r"## 18-Vector Threat Matrix", owasp_mapping, text, count=1)

with open("SECURITY.md", "w", encoding="utf-8") as f:
    f.write(text)
