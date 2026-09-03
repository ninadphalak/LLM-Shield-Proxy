# Glossary

Brief definitions for technical terms used across the project documentation.

## Privacy and Streaming
| Term | Definition |
| :--- | :--- |
| **Zero Egress** | The guarantee that identified, unredacted sensitive values will not be transmitted to the upstream provider. |
| **PII / PHI / PCI** | Personally Identifiable Information, Protected Health Information, Payment Card Industry data. |
| **Redaction / Masking** | Removing or replacing sensitive data before transmission. |
| **Rehydration** | The process of replacing a masked placeholder (e.g., `[EMAIL_1]`) in the LLM response with the original sensitive value before returning it to the client. |
| **SSE (Server-Sent Events)** | An HTTP streaming format used by LLMs to stream responses token-by-token. |
| **Fragmentation** | When a single logical word or PII entity is split across multiple streaming SSE chunks. |
| **Sliding-Window Buffer** | A mechanism to hold a small amount of streaming text in memory to detect if a placeholder was split across chunks. |

## Detection and Transformation
| Term | Definition |
| :--- | :--- |
| **Shannon Entropy** | A mathematical measure of randomness. Used to heuristically identify unstructured secrets like API keys. |
| **NER (Named-Entity Recognition)** | An NLP model technique used to identify entities like names, organizations, or locations in text. |
| **ONNX** | A portable machine learning model format used to run the local NER model. |
| **DFA Regex (`google-re2`)** | A regular expression engine that guarantees linear-time execution, preventing ReDoS (Regex Denial of Service) attacks. |
| **MCP (Model Context Protocol)** | A protocol standardizing how LLMs discover and interact with external tools and data sources. |

## Access and Network Security
| Term | Definition |
| :--- | :--- |
| **RBAC** | Role-Based Access Control. |
| **SSRF (Server-Side Request Forgery)** | An attack where the LLM is manipulated into making unauthorized network requests to internal systems. |
| **DNS Rebinding** | An SSRF evasion technique where an attacker rapidly changes the IP address associated with a domain name. |
| **mTLS (Mutual TLS)** | A security protocol where both the client and the server cryptographically verify each other's identity using certificates. |

## Cryptography and Audit Evidence
| Term | Definition |
| :--- | :--- |
| **AES-256-GCM** | A symmetric encryption algorithm used for stateless PII masking. |
| **SHA-256 Hash Chain** | A linked list of audit events where each event contains the cryptographic hash of the previous one, making deletions or reordering detectable. |
| **Ed25519** | A fast, highly secure public-key signature algorithm used to sign audit logs. |
| **WORM Storage** | Write-Once, Read-Many storage. Data cannot be modified or deleted after writing (e.g., AWS S3 Object Lock). |
| **OSCAL** | A standardized, machine-readable format for documenting security controls and compliance posture (NIST SP 800-53). |
