# LLM-Shield - Enterprise Privacy Redaction Engine

**LLM-Shield** is an open-source, zero-egress middleware proxy that intercepts OpenAI-compatible LLM API requests, redacts Personally Identifiable Information (PII) before it leaves your local infrastructure, and deterministically re-hydrates real-time SSE streaming responses without breaking stream latency.

Designed for enterprise privacy compliance (**SOC 2 / HIPAA**).

---

## ⚡ Core Features

- **Zero Latency Streaming:** Sliding-window tag-safety buffer intercepts SSE streams delta-by-delta without buffering full requests or responses.
- **Zero Cloud / Zero Egress:** 100% local processing. No external API calls for PII detection.
- **Two-Tier PII Cascade Engine:**
  - **Tier 1 (Sub-millisecond Regex):** SSNs, Credit Cards, Email Addresses, Phone Numbers, IPv4/IPv6, API Keys.
  - **Tier 2 (NER Engine):** Person Names and unstructured entities.
- **Deterministic Re-Hydration Vault:** Swaps PII with session-bound tokens (e.g. `Sarah` -> `[PERSON_1]`). Maps back deterministically when the LLM streams responses. Supports request-scoped and session-scoped (`X-Session-ID`) vaults.
- **SOC 2 Structured Audit Logging:** Emits JSON structured audit logs for compliance monitoring.
- **Anonymous Volumetric Telemetry:** Opt-out (`TELEMETRY_OPT_OUT=true`) telemetry worker collecting aggregated volumetric metrics with an explicit zero-PII guarantee.

---

## 🚀 Quickstart

### Running via Docker Compose

```bash
docker-compose up -d
```

### Running via Python / Uvicorn

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Usage with OpenAI Client

Simply point your base URL to LLM-Shield (`http://localhost:8000/v1`):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-api-key"
)

response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "Contact Sarah Connor at sarah@example.com"}
    ],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

---

## 🧪 Testing

Run the full automated test suite:

```bash
py -m pytest tests/
```

---

🏢 Using LLM-Shield in Production?

We are actively working with enterprise security teams to map out advanced compliance features. If your startup or organization is using LLM-Shield to unblock LLM streaming or pass SOC 2/HIPAA audits, I would love to hear from you.

Email the core maintainer at ninad.phalak@gmail.com  to share your feedback, request a feature, or feature your team as a case study.
