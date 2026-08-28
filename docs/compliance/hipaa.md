# HIPAA Compliance (Security & Transmission Rules)

## Overview: Safeguarding ePHI in the Generative AI Era

The Health Insurance Portability and Accountability Act (HIPAA), specifically the Security Rule and Transmission Security standard (45 CFR § 164.312), mandates that Covered Entities implement technical security measures to guard against unauthorized access to electronic Protected Health Information (ePHI) that is being transmitted over an electronic communications network.

Sending raw patient data to multi-tenant, public LLM endpoints (like standard OpenAI or Anthropic APIs) fundamentally violates these transmission and access rules. The LLM-Shield-Proxy ensures that zero ePHI leaves the secure boundaries of the enterprise VPC.

## Elimination of ePHI Egress

The primary mechanism for HIPAA compliance is the absolute pre-egress sanitization of all data streams.

### Tier-1 & Tier-2 Local Redaction
Before any prompt is dispatched to a third-party cloud LLM, it is processed locally:
- **Tier 1 (Structured Identifiers):** Utilizing a pre-compiled C++ `google-re2` DFA regex engine, the proxy executes O(N) linear time scans to instantly redact structured ePHI such as Social Security Numbers, Medical Record Numbers, phone numbers, and IP addresses. Custom regular expressions (BYOR) can be added via `custom_regex.yaml` to target specific internal hospital IDs.
- **Tier 2 (Unstructured Data):** A vectorized Shannon Entropy scanner operates at <6 µs to detect and redact unstructured high-entropy identifiers.

### Clinical Context Awareness (Tier-3 ONNX BERT-NER)
Medical data is often conversational and unstructured (e.g., doctor's notes). The proxy utilizes a **Quantized ONNX BERT-NER** model executing natively in-memory.
- **Bring Your Own Model (BYOM):** The architecture natively supports the ingestion of specialized healthcare models such as **BioBERT** and **ClinicalBERT**. This allows the proxy to achieve high-accuracy, context-aware entity extraction for medical conditions, patient names, and pharmaceutical regimens without relying on external NLP APIs.

## Transmission Security (45 CFR § 164.312)

If a use case requires the LLM to reference a specific patient entity without knowing who the patient is, the proxy employs **In-Band Stateless Synthetic**.

### Encrypted In-Transit Envelopes
- Detected ePHI is masked using **AES-256-GCM envelope encryption** directly within the payload.
- The external LLM receives a cryptographically secure cipher-token (e.g., `[[AES:GCM:8f7a9...]]`). It processes the clinical reasoning based on the surrounding text, and the proxy decrypts the cipher-token dynamically as the SSE stream returns to the clinician.
- This guarantees that no usable ePHI is transmitted to or stored by the external LLM provider, perfectly aligning with transmission security rules.

*(Reference the [Architecture & Cryptographic Data Flow](../../ARCHITECTURE.md) for deeper implementation details on the cryptographic lifecycle).*
