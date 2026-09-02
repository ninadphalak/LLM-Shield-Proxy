# Streaming-Privacy Gateway Architecture

## Abstract

LLM-Shield-Proxy is an Apache-2.0, self-hosted gateway for inspecting LLM and MCP traffic inside an
operator-controlled network. It provides tests for the configured-upstream boundary, incremental
SSE rehydration across tested fragment splits, policy checks for supported tool calls, and audit
metadata that excludes configured sensitive values.

The gateway can support technical controls used in SOC 2, HIPAA, EU AI Act, and ISO/IEC 42001 programs. It does not certify a system or organization as compliant.

## 1. Boundary and threat model

The protected boundary is the serialized request presented to the configured upstream transport after enabled transformations. **Zero egress** means that known unredacted protected values do not appear at that boundary. It does not mean that the proxy performs no network communication: it forwards the transformed request to the operator-configured upstream.

The current conformance model covers:

- protected values fragmented across input and SSE transport chunks;
- valid OpenAI-compatible SSE framing, including UTF-8 code points split across chunks;
- exact placeholder rehydration without exposing protected test values in the report;
- tool-call policy decisions;
- hash-chain continuity, Ed25519 verification, and a tamper negative control;
- distributional component timings and bounded-buffer/allocation observations.

See the [Streaming Privacy Gateway Conformance Specification v1.0.0](/docs/conformance/specification-v1) for normative requirements and explicit exclusions.

## 2. Streaming data plane

### 2.1 Inbound transformation

The text path combines structured-pattern detection, a Shannon-entropy heuristic for secret candidates, and an optional locally selected ONNX entity model. Structured JSON-RPC and MCP payloads are traversed as data structures so masking does not corrupt JSON syntax.

Detector quality is corpus- and configuration-dependent. The project does not claim universal recall or F1. Any detection-quality report should publish the labeled corpus, sampling method, splits, selected model and thresholds, error taxonomy, and confidence intervals.

### 2.2 Fragment-safe SSE rehydration

The `SSERehydrationBuffer` retains only the suffix that can still begin a known placeholder. Its retained-text bound is derived from the longest active placeholder rather than the total stream length. The proxy parses completed SSE events, rehydrates values incrementally, and preserves the terminal `[DONE]` marker.

The current conformance harness reports p50, p95, p99, mean, and standard deviation for declared in-process operations without asserting a universal threshold or calling them total proxy overhead.

## 3. MCP governance plane

The streaming tool parser extracts targeted `name` and `method` values across arbitrary chunk boundaries. Resolved policy can allow or deny calls without requiring the complete response to be retained indefinitely. Length, nesting, and catalog limits bound adversarial state.

OPA, Redis, and Vault integrations are deployment options, not evidence that an external policy system is configured correctly. Pilot assessments must exercise allowed, denied, unavailable-resolver, and malformed-input cases in the intended failure mode.

## 4. Audit and compliance evidence plane

Each emitted audit record can contain a predecessor SHA-256 hash, monotonic sequence, chain identifier, and Ed25519 signature. Offline verification detects changed records, insertions, reordering, sequence gaps, and unexpected signing keys within the evidence supplied.

The default `best_effort` mode uses a bounded queue and may drop events under backpressure. It generates a new process-local chain after restart and uses an ephemeral signing key unless a stable key is configured. Opt-in `durable` and `required` modes append and acknowledge local JSONL, request `fsync`, and recover chain state after restart.

These mechanisms are **tamper-evident, not storage-level WORM**. The project can verify independently ordered worker chains and sign a common terminal-state checkpoint. A complete evidence-grade deployment still needs independently configured immutable retention, external checkpoint anchoring, and production key custody and rotation. The [audit contract](/docs/features/enterprise-auditing-compliance/worm-compliant-audit-logging-with-hash-chaining) documents those boundaries.

The shared OSCAL builder emits OSCAL 1.2 `assessment-results` artifacts. Runtime exports use fresh document and result UUIDs on every call; offline assessments can supply deterministic UUIDs for byte-reproducible reports. OSCAL output supports control assessment and exchange-it does not prove that a control operated effectively.

## 5. Reproducible evaluation

Run:

```bash
llm-shield-proxy benchmark --iterations 10000 --json-out conformance.json
```

The report covers seven versioned domains: fragmentation safety, raw-PII egress, SSE validity, rehydration fidelity, audit integrity, latency measurement, and memory boundedness. Local timing excludes ASGI, HTTP/TLS, networks, model time, concurrency, and durable-audit I/O. Service-level claims require the separate load protocol, raw artifacts, repeated trials, and independent reproduction.

## 6. Conclusion

LLM-Shield-Proxy provides an open implementation and an open test contract for streaming privacy at the LLM boundary. Its value should be evaluated from reproducible artifacts and explicit threat boundaries-not absolute performance, compliance, or detection slogans.
