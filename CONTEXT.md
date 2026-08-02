# Project: LLM-Shield-Proxy (Enterprise Privacy Redaction Engine)

## 1. Project Objective
Build an open-source, stateless middleware proxy that intercepts LLM API requests, redacts Personally Identifiable Information (PII) before it leaves the local infrastructure, and deterministically re-hydrates the response. This must be an enterprise-grade tool designed for extreme privacy compliance (SOC 2 / HIPAA).

## 2. Core Engineering Constraints (CRITICAL)
- **Zero Latency Streaming:** The proxy MUST NOT buffer the full request or response. It must use a sliding-window buffer to analyze tokens on the fly. Real-time Server-Sent Events (SSE) streaming must remain perfectly intact.
- **Zero Cloud/Zero Egress:** The proxy must run 100% locally. Do not route data to external APIs (like AWS Macie) for PII detection. Use local Regex patterns and lightweight local NLP (e.g., Presidio or an in-memory ONNX model) for Named Entity Recognition.
- **Deterministic Masking & Re-hydration:** PII must be swapped for session-bound tokens. (e.g., "Sarah" becomes `[PERSON_1]`). The external LLM receives `[PERSON_1]`. When the LLM streams the answer back containing `[PERSON_1]`, the proxy must intercept it and re-hydrate it back to "Sarah" before passing it to the frontend.

## 3. Tech Stack Requirements
- **Language/Framework:** Python with FastAPI (for robust asynchronous streaming support).
- **Compatibility:** Must natively accept standard OpenAI SDK payload formats.
- **Packaging:** Must be containerized via Docker for 1-click enterprise deployment.