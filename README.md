# LLM-Shield-Proxy - Enterprise Privacy Redaction Engine

[![PyPI Version](https://img.shields.io/pypi/v/llm-shield-proxy.svg)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)

**LLM-Shield-Proxy** is an open-source, zero-egress middleware proxy that intercepts OpenAI-compatible LLM API requests, redacts Personally Identifiable Information (PII) before it leaves your local infrastructure, and deterministically re-hydrates real-time SSE streaming responses without breaking stream latency.

Designed for enterprise privacy compliance (**SOC 2 / HIPAA**).

Author & Core Maintainer: **Ninad Phalak** (`ninad.phalak@gmail.com`)

---

## 🏗️ Architecture & Data Flow

```mermaid
flowchart LR
    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0369a1,font-weight:bold;
    classDef proxyEngine fill:#f8fafc,stroke:#475569,stroke-width:2px,color:#0f172a,font-weight:bold;
    classDef piiSecurity fill:#fef2f2,stroke:#ef4444,stroke-width:2px,color:#991b1b,font-weight:bold;
    classDef vault fill:#fffbebe,stroke:#f59e0b,stroke-width:2px,color:#92400e,font-weight:bold;
    classDef upstream fill:#f3e8ff,stroke:#9333ea,stroke-width:2px,color:#6b21a8,font-weight:bold;

    UserApp["👤 User Application\n(OpenAI / LangChain SDK)"]:::client

    subgraph SecurityMoat ["🛡️ Zero-Egress Local Environment (Apache 2.0 Licensed)"]
        direction LR
        FastAPIProxy["⚡ FastAPI Catch-All Proxy\n(/{path:path})"]:::proxyEngine

        subgraph CascadeEngine ["🔒 Two-Tier PII Cascade Engine"]
            Tier1["Tier 1: Compiled Regex"]:::piiSecurity
            Tier2["Tier 2: Quantized ONNX NER"]:::piiSecurity
            Tier1 --> Tier2
        end

        VaultStore[("🔑 Session Vault Store\n(Deterministic Tokens)")]:::vault
        LookaheadBuffer["⏱️ Sliding-Window Lookahead Buffer\n(Prevent SSE Tag Leaks)"]:::proxyEngine
        Rehydrator["🔄 Stream Re-hydrator\n(Token -> Original Value)"]:::proxyEngine
    end

    UpstreamLLM["☁️ Upstream LLM\n(OpenAI / Anthropic / vLLM)"]:::upstream

    %% Inbound Flow (Prompt Sanitization)
    UserApp -- "1. Inbound Request (Raw Prompt)" --> FastAPIProxy
    FastAPIProxy -- "2. Scan Payload" --> Tier1
    Tier2 -- "3. Store Keys" --> VaultStore
    Tier2 -- "4. Redacted JSON Payload" --> UpstreamLLM

    %% Outbound Flow (Streaming De-redaction)
    UpstreamLLM -. "5. SSE Stream Deltas" .-> LookaheadBuffer
    LookaheadBuffer -- "6. Tag-Safe Buffer" --> Rehydrator
    Rehydrator <--> VaultStore
    Rehydrator -. "7. Sanitized Real-Time Stream" .-> UserApp

    style SecurityMoat fill:#f8fafc,stroke:#0284c7,stroke-width:2px,stroke-dasharray: 5 5,color:#0f172a
    style CascadeEngine fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
```

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

## 🛠️ Quickstart

### Installation

Install the package from PyPI:

```bash
pip install llm-shield-proxy
```

#### 1. Start the Proxy

Run the proxy locally via Docker or Uvicorn. No external database required for the open-source core.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
or via Docker Compose:
```bash
docker-compose up -d
```

#### 2. Update your Application (1-Line Change)

Point your existing OpenAI SDK `base_url` to your local LLM-Shield-Proxy instance.

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-openai-api-key",
    base_url="http://localhost:8000/v1" # Point to LLM-Shield-Proxy
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

## 🏢 Using LLM-Shield-Proxy in Production?

We are actively working with enterprise security teams to map out advanced compliance features. If your startup or organization is using LLM-Shield-Proxy to unblock LLM streaming or pass SOC 2/HIPAA audits, I would love to hear from you.

Email the core maintainer at ninad.phalak@gmail.com to share your feedback, request a feature, or feature your team as a case study.
