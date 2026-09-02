# Streaming Privacy Gateway Conformance Specification v1.0.0

**Status:** Public version 1.0.0

**Report schema:** `llm-shield.streaming-privacy-conformance/v1.0.0`

**License:** Apache License 2.0

This specification defines repeatable tests and minimum reporting requirements for a gateway that transforms protected data before a configured LLM upstream and reconstructs authorized values in a streaming response. The keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Security boundary and terminology

**Configured upstream boundary** means the exact serialized request bytes presented by the gateway to its configured upstream network transport after all enabled transformations. In this specification, **zero egress** means:

> Unredacted protected values detected under the declared configuration and vector set do not appear at the configured upstream boundary.

It does not mean that a detector recognizes every possible real-world identifier, that encrypted/tokenized representations do not leave the network, or that no other deployment component can transmit data.

**Protected value** is a value explicitly declared in the test vector. **Placeholder** is the gateway representation mapped to that value. **Rehydration** is authorized reconstruction on the client-facing response path.

## 2. Required report metadata

A conforming report MUST include:

- specification name/version and report-schema identifier;
- generation timestamp and implementation version;
- exact source revision, or the literal `unknown` with a limitation;
- Python/runtime, operating system, architecture/processor when available;
- declared iteration count and measurement units;
- one result object for every domain in Section 3;
- limitations and excluded components;
- no protected vector value, reconstructed value, prompt, or reversible mapping.

## 3. Normative domains

### SPG-FRAG-1: Fragmentation safety

The runner MUST test every two-part character split of at least one registered placeholder, including empty-prefix and empty-suffix partitions, and MUST test one-character-at-a-time delivery. No partial placeholder may be released as a reconstructed value before the match is unambiguous. The report MUST publish partition count and failure metadata without publishing protected values.

### SPG-EGRESS-1: Raw protected-data egress

The runner MUST serialize the post-transformation payload exactly as it would be presented at the configured upstream boundary and search for every declared protected value. The report MUST list entity classes and leak status, but MUST NOT contain the test values or transformed payload.

This test is configuration-scoped. A pass is not a population-level recall claim.

### SPG-SSE-1: SSE validity

For the declared provider shape, every nonterminal `data:` event MUST parse as the expected JSON object, exactly one terminal `[DONE]` marker MUST be preserved, and the stream MUST terminate with valid event framing. At least one UTF-8 multibyte character MUST be split at a byte boundary and reconstructed without decoder corruption.

### SPG-REHYDRATE-1: Rehydration fidelity

For the declared mapping and authorization context, concatenated client-visible content MUST exactly equal the expected value and surrounding text. No placeholder may remain in client-visible content. Reports MUST expose equality/status only, not either value.

### SPG-AUDIT-1: Audit integrity

The runner MUST verify a minimum two-record signed chain with a stable chain identifier and consecutive sequence values. It MUST validate record hashes, predecessor linkage, Ed25519 signatures, and the public-key fingerprint. It MUST modify a signed record as a negative control and MUST observe verification failure.

This test establishes verifier behavior. It does not make local storage immutable, detect deletion of an unanchored suffix, validate production key custody, or establish legal non-repudiation.

### SPG-LATENCY-1: Latency reporting

The runner MUST publish warmup behavior, iteration count, operation scope, clock unit, mean, p50, p95, and p99. It MUST distinguish no-op, empty-vault buffer, and protected-placeholder buffer paths. Local in-process results MUST NOT be described as total proxy overhead.

Version 1.0.0 sets no universal pass threshold. A deployment MAY publish a preregistered threshold in a separate profile.

This domain is scored by publication, not by a `checks` entry. It contributes no pass/fail result object; a runner that omits the required distributions produces an invalid report rather than a failed check.

### SPG-MEMORY-1: Memory reporting and bounded state

The runner MUST demonstrate that retained placeholder-prefix state does not exceed the declared token-derived bound. Allocation observations MUST be labeled as allocations, not RSS. A process-memory claim MUST use a separate production profile specifying sampling tool, lifecycle phase, workload, duration, concurrency, installation extras, and peak-versus-steady-state semantics.

Version 1.0.0 sets no universal RSS threshold.

## 4. Pass calculation

The top-level result is `passed: true` only when all six scored domains pass. SPG-LATENCY-1 is a publication requirement, not a scored domain. Its old check only verified that elapsed times were non-negative, so the check was removed. The required latency measurements must still be published.

A memory pass means that the report includes the required measurement and demonstrates the declared bound. It does not establish any unstated performance threshold.

## 5. Reproducibility and publication

A published result SHOULD include the JSON report, tagged source, dependency lock, command, environment description, checksums, and all failed repetitions. Independent reproduction MUST identify the reproducer and must not replace the implementation owner's artifact.

Comparative reports MUST run candidates on the same host, client, vectors, warmup, iteration count, and scope. Product names, sponsorship, excluded components, and conflicts of interest MUST be disclosed.

## 6. Versioning

- Patch versions clarify text without changing required report fields or test semantics.
- Minor versions add backward-compatible domains or metadata.
- Major versions may change pass semantics or remove/rename required fields.

Reports MUST identify the exact specification version. Consumers must not silently interpret a report under a different major version.
