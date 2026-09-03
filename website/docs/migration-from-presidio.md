# Migrating from Microsoft Presidio

Microsoft Presidio is an in-process SDK for detecting PII. It is not an LLM gateway. Using Presidio on a streaming LLM path requires building custom integration code to handle SSE chunking, state management, and policy enforcement.

LLM-Shield-Proxy is a standalone reverse proxy. It centralizes PII redaction and provides built-in support for streaming LLM traffic (SSE). 

## Comparison

| Feature | Microsoft Presidio | LLM-Shield-Proxy |
| :--- | :--- | :--- |
| **Architecture** | In-process Python SDK | Standalone reverse proxy / sidecar |
| **Streaming (SSE)** | Requires custom application logic | Native sliding-window SSE rehydration |
| **Secret Detection** | Requires writing custom recognizers | Built-in Shannon entropy scanner for unformatted keys |
| **Auditing** | None built-in | Hash-chained, Ed25519-signed audit logs |

## Step 1: Client Reconfiguration

To test LLM-Shield-Proxy, simply point your LLM client at the proxy instead of the upstream provider.

```python
# Before (Direct to provider, relying on inline Presidio)
client = OpenAI(api_key="sk-...", base_url="https://api.openai.com/v1")

# After (Routing through LLM-Shield-Proxy)
client = OpenAI(api_key="sk-...", base_url="http://localhost:8000") 
```

## Step 2: Entity Mapping

If your compliance policies depend on specific Presidio entity types, use this table to map them to LLM-Shield-Proxy entities.

| Presidio Entity | LLM-Shield-Proxy Entity | Detection Engine |
| :--- | :--- | :--- |
| `PERSON` | `PERSON` | Tier 3 (requires an ONNX model; no fallback detector) |
| `EMAIL_ADDRESS` | `EMAIL` | Tier 1 (google-re2 Regex) |
| `PHONE_NUMBER` | `PHONE` | Tier 1 (google-re2 Regex) |
| `US_SSN` | `SSN` | Tier 1 (google-re2 Regex) |
| `CREDIT_CARD` | `CREDIT_CARD` | Tier 1 (google-re2 Regex) |
| `IP_ADDRESS` | `IP_ADDRESS` | Tier 1 (google-re2 Regex) |
| `MEDICAL_LICENSE` | `MRN` | Tier 1 (google-re2 Regex) |
| `CRYPTO` | `AWS_API_KEY`, `GITHUB_PAT`, `JWT_TOKEN` | Tier 1 (google-re2 Regex) |
| Custom (high-entropy) | `SECRET_KEY` | Tier 2 (Shannon Entropy) |

*Note: Tier 1 (Regex) and Tier 2 (Entropy) are enabled by default. Tier 3 (NER) is opt-in and requires the `[ner]` extra installation.*
