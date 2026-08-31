# HIPAA Compliance (Security & Transmission Rules)

## Overview: Safeguarding ePHI in the Generative AI Era

The Health Insurance Portability and Accountability Act (HIPAA), specifically the Security Rule and Transmission Security standard (45 CFR § 164.312), mandates that Covered Entities implement technical security measures to guard against unauthorized access to electronic Protected Health Information (ePHI) that is being transmitted over an electronic communications network.

Sending ePHI to an LLM service requires an organization-specific legal, contractual, security, and risk analysis. LLM-Shield-Proxy can transform configured ePHI patterns inside an operator-controlled deployment and test declared values at the configured upstream boundary; it cannot establish that a deployment is HIPAA compliant or that every ePHI value was detected.

## Elimination of ePHI Egress

The proxy can support one pre-upstream transformation control for configured traffic. HIPAA compliance depends on the organization's full administrative, physical, technical, contractual, and operational safeguards; detector coverage and routing boundaries require independent validation.

### Tier-1 & Tier-2 Local Redaction
Before any prompt is dispatched to a third-party cloud LLM, it is processed locally:
- **Tier 1 (Structured Identifiers):** Pre-compiled `google-re2` patterns scan for configured formats such as Social Security Numbers, Medical Record Numbers, phone numbers, and IP addresses. Detection quality depends on patterns and corpus; custom RE2-compatible rules can be added via `custom_regex.yaml`.
- **Tier 2 (Unstructured Data):** A Shannon entropy heuristic identifies high-entropy secret-like candidates for configured handling.

### Clinical Context Awareness (Tier-3 ONNX BERT-NER)
Medical data is often conversational and unstructured (e.g., doctor's notes). The proxy utilizes a **Quantized ONNX BERT-NER** model executing natively in-memory.
- **Bring Your Own Model (BYOM):** The architecture natively supports the ingestion of specialized healthcare models such as **BioBERT** and **ClinicalBERT**. This allows the proxy to achieve high-accuracy, context-aware entity extraction for medical conditions, patient names, and pharmaceutical regimens without relying on external NLP APIs.

## Transmission Security (45 CFR § 164.312)

If a use case requires the LLM to reference a specific patient entity without knowing who the patient is, the proxy employs **In-Band Stateless Synthetic**.

### Encrypted In-Transit Envelopes
- Detected ePHI is masked using **AES-256-GCM envelope encryption** directly within the payload.
- The external LLM receives a cryptographically secure cipher-token (e.g., `[[AES:GCM:8f7a9...]]`). It processes the clinical reasoning based on the surrounding text, and the proxy decrypts the cipher-token dynamically as the SSE stream returns to the clinician.
- Boundary conformance tests can verify that declared unredacted ePHI fixtures do not reach the configured upstream. Detection coverage and the organization's HIPAA obligations require separate validation.

*(Reference the [Architecture & Cryptographic Data Flow](/docs/architecture) for deeper implementation details on the cryptographic lifecycle).*
