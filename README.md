# LLM-Shield - Enterprise Privacy Redaction Engine

[![PyPI Version](https://img.shields.io/pypi/v/llm-shield-proxy.svg)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)

**LLM-Shield** is an open-source, zero-egress middleware proxy that intercepts OpenAI-compatible LLM API requests, redacts Personally Identifiable Information (PII) before it leaves your local infrastructure, and deterministically re-hydrates real-time SSE streaming responses without breaking stream latency.

Designed for enterprise privacy compliance (**SOC 2 / HIPAA**).

Author & Core Maintainer: **Ninad Phalak** (`ninad.phalak@gmail.com`)

---

## ⚡ Core Features

- **Zero Latency Streaming:** Sliding-window tag-safety buffer intercepts SSE streams delta-by-delta without buffering full requests or responses.
- **Zero Cloud / Zero Egress:** 100% local processing. No external API calls for PII detection.
- **Two-Tier PII Cascade Engine:**
  - **Tier 1 (Sub-millisecond Regex):** SSNs, Credit Cards, Email Addresses, Phone Numbers, IPv4/IPv6, API Keys.
  - **Tier 2 (NER Engine):** Person Names and unstructured entities.
- **Deterministic Re-Hydration Vault:** Swaps PII with session-bound tokens (e.g., `Sarah` -> `[PERSON_1]`). Maps back deterministically when the LLM streams responses. Supports request-scoped and session-scoped (`X-Session-ID`) vaults.
- **SOC 2 Structured Audit Logging:** Emits JSON structured audit logs for compliance monitoring.
- **Opt-In Telemetry:** Strictly opt-in (`TELEMETRY_ENABLED=false` by default) telemetry worker collecting aggregated volumetric metrics with an explicit zero-PII guarantee.

---

## 📦 Installation

Install `llm-shield-proxy` directly from PyPI via `pip`:

```bash
pip install llm-shield-proxy
```


Or install locally in editable mode:

```bash
pip install -e .
```

---

## 🚀 Quickstart

### Running via Python / Uvicorn

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Running via Docker Compose

```bash
docker-compose up -d
```

### Usage with OpenAI Client

Point your base URL to LLM-Shield (`http://localhost:8000/v1`):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-api-key"
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
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

## 🏢 Using LLM-Shield in Production?

We are actively working with enterprise security teams to map out advanced compliance features. If your startup or organization is using LLM-Shield to unblock LLM streaming or pass SOC 2/HIPAA audits, I would love to hear from you.

Email the core maintainer at ninad.phalak@gmail.com  to share your feedback, request a feature, or feature your team as a case study.
