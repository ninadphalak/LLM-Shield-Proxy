# LLM-Shield-Proxy: Enterprise Compliance & Trust Mapping

LLM-Shield-Proxy is designed specifically to help enterprise engineering teams adopt Generative AI without violating data privacy regulations. 

Because LLM-Shield operates as a zero-egress, stateless middleware proxy deployed entirely within your own Virtual Private Cloud (VPC), it inherently bypasses the major compliance risks associated with third-party SaaS security tools. 

This document maps LLM-Shield's architectural features directly to standard enterprise compliance frameworks.

## 🏥 HIPAA (Health Insurance Portability and Accountability Act)
For healthcare organizations (Covered Entities) and digital health startups, streaming Protected Health Information (PHI) to external LLM APIs (like OpenAI or Anthropic) without a Business Associate Agreement (BAA) is a direct HIPAA violation. 

LLM-Shield allows organizations to utilize LLMs safely by deterministically removing PHI before it leaves the hospital's network.

| HIPAA Requirement | How LLM-Shield Solves It |
| :--- | :--- |
| **Transmission Security**<br>*(45 CFR § 164.312(e)(1))* | The proxy intercepts outbound payloads locally. The 2-Tier Cascade engine (Regex + local ONNX NER) deterministically masks PHI into tags (e.g., `[PERSON_1]`). No raw PHI is transmitted over the public internet to third-party APIs. |
| **Audit Controls**<br>*(45 CFR § 164.312(b))* | Built-in structured JSON logging (`app/audit.py`) records the timestamp, session ID, and summary of redactions applied to every request, compatible with SIEMs like Splunk or Datadog for compliance auditing. |
| **Data Integrity & Storage** | LLM-Shield is strictly stateless. The ephemeral mapping of PHI-to-tags is held in a volatile Redis Vault with a strict Time-To-Live (TTL). Once the session ends, the PHI is wiped. No permanent databases are created. |

## 🛡️ SOC 2 Type II & HITRUST
For B2B SaaS companies and healthcare networks, LLM-Shield satisfies the strict Privacy, Security, and Confidentiality criteria required to pass SOC 2 and HITRUST audits.

| Trust Services Criteria | How LLM-Shield Solves It |
| :--- | :--- |
| **Logical Access Security**<br>*(CC6.1)* | LLM-Shield requires zero external API keys for the redaction engine. It runs 100% locally in your VPC. Third-party SaaS security vendors do not get access to your data streams. |
| **Boundary Protection**<br>*(CC6.6)* | Deploys as an internal microservice sidecar. It sits safely behind your edge API gateway (e.g., NGINX, Traefik, AWS ALB), allowing your edge to handle FIPS-compliant TLS termination while LLM-Shield handles localized data sanitization. |
| **Privacy / Data Minimization**<br>*(P3.1)* | PII is never logged to disk or stdout. The proxy exclusively logs volumetric telemetry (token counts, redaction events) ensuring no user data leaks into Datadog or ELK logging stacks. |

## 🌍 GDPR (General Data Protection Regulation)
LLM-Shield-Proxy's zero-egress architecture natively supports GDPR's **Data Minimization (Article 5)** and **Privacy by Design (Article 25)** principles. Because the data processing occurs entirely within your localized European infrastructure, cross-border data transfer liabilities to non-EU LLM endpoints are severely mitigated. *(Note: EU-specific regex formatting, such as IBANs and UK NINs, is actively being mapped in our i18n roadmap).*

## 🔒 Supply Chain & Operational Security
We take the security of this open-source infrastructure seriously.

*   **Zero External Dependencies for Inference:** The proxy does not pull heavy, unaudited weights at runtime. The compiled Regex and quantized ONNX engines execute entirely within the containerized boundary.
*   **Automated Vulnerability Scanning:** This repository utilizes GitHub-native dependency scanning (Dependabot) to ensure underlying Python libraries (like `fastapi` and `uvicorn`) are actively patched against CVEs.
*   **Reproducible Builds:** Provided Dockerfiles lock in specific base images (`python:3.9-slim`), ensuring predictable, bloat-free deployments with a `<24MB` resident RAM footprint.
*   **Cryptographic Integrity:** Release commits and tags are GPG-signed by the core maintainer to prevent supply-chain spoofing.

> **Disclaimer:** *LLM-Shield-Proxy provides technical safeguards to assist with compliance, but it is not a substitute for legal counsel. Organizations are responsible for configuring their own environments and conducting their own independent audits.*
