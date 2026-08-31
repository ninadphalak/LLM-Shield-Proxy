# Stability Tiers

Every feature in this project is assigned a stability tier. The tier answers one question:

> **How much independent evidence exists that this works outside the maintainer's machine?**

Tiers describe *verification status*, not code quality. A feature in Experimental may be
correct; it simply has not been exercised end-to-end against the infrastructure it targets.

| Tier | Meaning | What you can rely on |
|---|---|---|
| **Supported** | Runs from `pip install llm-shield-proxy` with no external infrastructure. Covered by the automated test suite and, where applicable, by the conformance harness. | Behavior is reproducible by a third party in minutes. Breaking changes follow the deprecation policy below. |
| **Beta** | Covered by tests, but requires ordinary external infrastructure (Redis, an ONNX model file, an OTel collector, a provider account) or depends heavily on operator configuration. | The code path is exercised. Your topology, configuration, and failure modes are not. Integration-test before production. |
| **Experimental** | Targets infrastructure that has not been exercised end-to-end in this repository, or implements a deliberate subset of a larger protocol. | Treat as a reference implementation and a starting point. Do not place on a production traffic path without your own validation. |

## Why this exists

This project is young and has been developed primarily by one maintainer. A long feature list
from a young repository is a reason for suspicion, not confidence. Publishing which features
carry independent evidence — and which do not — is more useful than publishing a longer list.

Features are not removed when they land in Experimental. They are labeled so that you can tell
the difference between "this is proven" and "this is implemented."

## Supported

Verifiable with `pip install llm-shield-proxy` and no other dependencies.

**Detection and masking**
- Tier 1 pre-compiled regex engine (`google-re2`)
- Tier 2 Shannon entropy scanner
- Format-preserving synthetic masking
- In-band stateless cryptographic masking (AES-256-GCM, no datastore)
- 4-mode per-request masking pipeline (`SYNTHETIC`, `STRUCTURAL_TAG`, `SCRUB`, `STATELESS_CRYPTO`)
- Bring-your-own-regex custom rules
- JSON bomb / payload nesting depth protection
- Stateless mutation engine (AST-aware semantic firewall)
- Dynamic schema rewriting (OpenAI tool schemas)

**Streaming**
- SSE sliding-window rehydration buffer
- Bounded streaming JSON lexer
- Multi-provider request/event translators
- Anthropic adapter (documented subset)

**Conformance and evidence**
- Local implementation conformance profile (`llm-shield-proxy benchmark`)
- OpenAI-compatible HTTP gateway profile (`--target-base-url`)
- Tamper-evident audit hash chaining
- Ed25519-signed audit receipts and chain verification
- FIPS KAT self-tests and RFC 6902 differential audit records
- NIST OSCAL assessment-results generation
- Compliance-pack CLI export

**Request handling**
- SSRF / DNS-rebinding egress guard
- Security response headers
- Request-ID correlation and sanitization
- Graceful shutdown / drain lifecycle
- Request-scoped dynamic overrides
- Bounded exponential retries

## Beta

Exercised by tests; requires external infrastructure or substantial operator configuration.

- Tier 3 quantized ONNX BERT-NER (requires `ONNX_MODEL_PATH`; falls back to heuristics)
- Redis TTL vault
- Granular entity policy scopes
- Entity-weighted blast radius limits
- Composite agent-loop circuit breaker
- Request rate limiting and traffic-engineering controls
- HTTP/2 upstream connection pooling
- Provider failover routing and per-request override
- LLM FinOps meter and `stream_options` injection
- Edge-level agent identity enforcer (JWT / DPoP)
- Role-based policy-as-code with hot reload
- Component health probes and Prometheus alert rules
- Asynchronous OpenTelemetry tracing
- Canary prompt tripwires (requires `SHIELD_WATERMARK_SECRET`)
- Dynamic canary watermarking
- Stream digest receipt
- Applied role name in audit events
- Context-aware tool catalog pruner

## Experimental

Not exercised end-to-end against the infrastructure it targets, or a deliberate protocol subset.

| Feature | Why it is Experimental |
|---|---|
| Envoy `ext_proc` integration | Not validated against a pinned real Envoy container. Buffer modes, timeout policy, and long-TTFT behavior are unverified. |
| UDS socket TOCTOU hardening | Linux-only and coupled to the `ext_proc` path above. |
| Kubernetes mutating webhook | No cluster admission install has been performed. |
| Helm chart | No `helm template` render or cluster deployment has been performed in this repository. Sidecar injection is explicit opt-in. |
| HashiCorp Vault secrets and mTLS | No live Vault backend has been exercised. |
| OPA and Vault RBAC resolvers | Failure, staleness, refresh, and concurrency behavior are untested against live backends. |
| Scoped MCP JSON-RPC gateway | Implements `tools/list`, `tools/call`, and `resources/read` only. No initialization, capability negotiation, sessions, or GET/SSE channel. Not drop-in for arbitrary MCP SDKs. |
| Pluggable tool-call RBAC | Applies only to the MCP method subset above. The default in-memory resolver is permissive unless policy is supplied. |
| Dynamic MCP tool schema rewriting | Schema rewriting cannot compel a model or parser to echo the added fields. |
| Decision trace exporter | A library primitive. Runtime proxy routes do not invoke it. |
| GRC webhook and file transport | Caller-wired primitives with no environment-based wiring or vendor connector. |
| Multi-provider upstream key registry | Matches four exact hostnames. Azure hostname/header handling is not implemented. |

## Deprecation policy

- **Supported** features change behavior only in a minor release, with a documented migration
  note, and never in a patch release.
- **Beta** features may change behavior in a minor release with a changelog entry.
- **Experimental** features may change or be removed in any release.

The conformance specification in `spec/` has its own versioning and governance process and is
not covered by this policy. See `website/docs/conformance/governance.md`.

## Moving a feature between tiers

A feature moves up when independent evidence exists — a passing end-to-end test against the real
infrastructure in CI, or a reproducible deployment by someone other than the maintainer. Open an
issue with the evidence and the tier will be updated.
