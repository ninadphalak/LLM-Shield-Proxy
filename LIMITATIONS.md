# Limitations and assurance boundaries

This document is the consolidated boundary statement for LLM-Shield-Proxy. Feature pages and
reports may add narrower constraints; none should imply a broader assurance than this document.

## Detection is not complete

- Regex, entropy heuristics, and optional NER models can produce false positives and false
  negatives. No population-level recall, precision, F1, or coverage rate applies without a
  labeled, representative corpus and a pinned configuration.
- The standard detector does not inspect pixels. Base64 inspection is bounded to documented
  text-sized candidates; large encoded bodies, arbitrary attachments, images, and unsupported
  provider fields can remain uninspected.
- Custom rules inherit the quality and scope of the supplied patterns.

## The privacy boundary is configured, not universal

- A configured-upstream test observes the serialized request presented to the selected upstream
  client. It is not packet capture and does not establish that other processes, SDKs, routes,
  telemetry, logs, plugins, or direct connections cannot transmit data.
- Traffic must actually be routed through the proxy. Network policy, identity, DNS, ingress,
  service-mesh, and egress controls are required where bypass prevention matters.
- “Zero egress” in project reports means that declared raw protected fixtures were absent at that
  tested boundary. It does not mean the proxy makes no network requests.

## Storage and memory depend on mode and deployment

- Stateless crypto avoids a mapping database but plaintext still exists in process memory during
  transformation. Keys, crash dumps, swap, observability, and runtime administration remain in
  scope.
- In-memory mappings live until cleanup, expiry, or process termination. Redis TTL makes keys
  eligible for expiry; it does not prove secure erasure from allocator memory, persistence files,
  replicas, backups, or snapshots.
- The package does not establish “zero data liability” or a universal process-RSS ceiling.
- **The streaming output ceiling is an anti-amplification bound, not an absolute memory cap, and
  two questions about it are open.** A single expanded streaming piece is limited to
  `MAX_PAYLOAD_SIZE_BYTES + MAX_SSE_LINE_LENGTH` (11 MiB by default), which fails closed on
  repeated-token amplification. But (1) that couples the output ceiling to the request-size
  limit, so raising the request limit raises the amplification ceiling with it, and (2) the
  rationale — "at most one accepted request's originals plus one accepted line of framing" —
  assumes every vault original arrived in the accepted request, which is unverified for
  session-scoped or custom vault population. Both are open policy questions, recorded here
  because they bound what the conformance profile's `memory_bounded` check means for this
  implementation. Neither is a known defect; neither has been closed.

## Cryptography has explicit key and token contracts

- `SHIELD_ENCRYPTION_KEY` is operator-provided key material for supported stateless encryption
  paths. Missing or invalid required key material fails startup or the applicable request path.
- AES-GCM rehydration requires the correct key, associated data, and an intact token. Model or
  middleware transformation can make recovery impossible.
- Application known-answer tests are not FIPS 140-3 module validation. Key generation, custody,
  access, rotation, revocation, backup, and destruction remain deployment responsibilities.

## Rehydration and structured output are conditional

- Rehydration covers registered substitutions that survive in the inspected response path.
  Paraphrased, truncated, normalized, encoded, or omitted values may not match.
- AST mutation preserves valid JSON for supported values, but it can change schema types for some
  array leaves. Schema rewriting does not force a model or provider to echo context fields.
- Provider adapters cover documented subsets. Tool calls, structured output, multimodal blocks,
  streaming events, errors, retries, cancellation, and model semantics require pinned integration
  tests.

## MCP and Envoy support are scoped

- `POST /v1/mcp` is a JSON-RPC gateway for selected methods. It is not currently a complete MCP
  Streamable HTTP implementation: initialization, capabilities, sessions, GET/SSE, and other
  methods are outside the route.
- Empty MCP allowlists deny every tool by default. `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY`
  explicitly changes that contract to permit every tool not named in `blocked_tools`; startup
  emits a critical warning for that permissive state. Verify the effective resolver, allowlist,
  blocklist, and egress policy before routing tool calls.
- Envoy `ext_proc` behavior depends on Envoy version, body modes, buffer limits, timeout/failure
  policy, UDS permissions, and the processor's supported protobuf contract.

## Audit and attestation evidence is bounded

- Signed, hash-linked audit records are tamper-evident evidence supplied to the verifier. Default
  delivery is best effort and can drop under pressure. Durable local JSONL is not WORM storage.
- Deleting an unanchored suffix cannot be detected from the shortened file alone. Immutable
  retention, external checkpoints, completeness monitoring, and independent key custody are
  separate controls.
- The legacy `proof_of_non_egress` audit event signs a digest of response-stream bytes observed by
  the application. It is not an ingress receipt, packet capture, response header, or proof of all
  network behavior.

## Canary and watermark signals are not attribution

- Literal tripwires can be transformed, omitted, forged by a party with key access, or triggered
  accidentally. Earlier bytes cannot be recalled after a later match.
- Zero-width watermarks can be stripped or changed by normalization, copying, editors, messaging
  systems, accessibility tools, and document conversion. A surviving marker is a correlation
  signal, not proof of who disclosed content or why. Watermark identity fingerprints are scoped
  by the operator-supplied `SHIELD_WATERMARK_SECRET`; enabling watermarking without it fails
  startup.

## Performance and availability are environment-specific

- Published microbenchmarks exclude components stated in their report, commonly ASGI, HTTP/TLS,
  networks, provider/model time, concurrency, and durable audit I/O.
- **No timing figure, speed multiplier, or latency comparison against another gateway is
  published anywhere in this repository.** An earlier internal diagnostic produced some; its
  runner and raw samples were not retained and an independent re-measurement of one component
  found a materially different magnitude, so the numbers were withdrawn rather than caveated. A
  performance claim here requires a versioned runner committed to the repository, run end to end
  against every gateway compared, with its raw output published beside it.
- There is no universal latency, throughput, availability, or memory guarantee. Rate limits,
  retries, failover, load shedding, connection pools, and graceful shutdown reduce selected risks
  but can still reject, duplicate, delay, or interrupt work.

## Compliance and certification are outside the package

- The project can support technical controls and evidence collection. It does not certify an
  organization or deployment as compliant with SOC 2, HIPAA, GDPR, PCI DSS, GLBA, the EU AI Act,
  NIST, ISO, or FIPS requirements.
- OSCAL exports, control mappings, signed receipts, and self-tests do not establish control design
  or operating effectiveness. Obtain organization-specific legal, security, privacy, and audit
  review.

## Evidence maturity

- Current published conformance results are maintainer self-tests unless labeled otherwise. No
  unaffiliated reproduction or production deployment should be inferred from a repository test.
- **Every published conformance row, including this project's own, reads `unreplicated`.** A
  target does not read as a verdict below 3 runs from 3 distinct submitters, and the maintainer's
  runs never count toward anyone's replication.
- Nothing binds a submitted conformance report to the run that produced it, and the shipped
  fixture is measurably gameable by a format-matching shim with no detector in it. Both are
  documented rather than hidden.
- Integration examples demonstrate configuration syntax and tested fixtures, not upstream
  maintainer endorsement or universal compatibility.
- Pending patent applications are not issued patents and make no representation about validity,
  scope, ownership disputes, or eventual grant.
