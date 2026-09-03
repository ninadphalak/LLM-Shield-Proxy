# HIPAA technical safeguard support

Sending electronic protected health information (ePHI) to an LLM service requires an
organization-specific legal, contractual, security, and risk analysis. LLM-Shield-Proxy can
support selected technical safeguards. It cannot determine whether an organization or deployment
is HIPAA compliant.

## Transmission controls

On supported request paths, the proxy can replace detected values before sending the transformed
request to the configured upstream. The conformance harness can test whether its declared fixtures
reached that upstream boundary. It does not prove that every ePHI value was detected or that no
other network path exists.

The proxy also supports inbound TLS/mTLS and outbound certificate verification or client
certificates. Operators should still configure trust roots, certificate identity mapping,
authorization, revocation, key custody, routing, and monitoring.

## Detection and masking

- Tier 1 uses configured RE2 patterns for structured shapes such as SSNs, medical-record numbers,
  phone numbers, and IP addresses.
- Tier 2 uses an entropy heuristic for selected secret-like candidates.
- Tier 3 name (PERSON) detection requires an operator-supplied ONNX model. If no model is loaded, name redaction is completely inactive and does not fall back to heuristics. Accuracy depends on the exact model, tokenizer, threshold, language, and clinical evaluation corpus.
- `STATELESS_CRYPTO` can encrypt selected detected values inside the payload with AES-256-GCM.
  Model changes or token loss can prevent rehydration.

None of these controls guarantees complete ePHI detection. Validate false negatives and false
positives with synthetic clinical fixtures before production use.

## Access and integrity evidence

Configured identity and policy resolvers can restrict supported operations. Hash-chained,
Ed25519-signed audit records can expose changes within the supplied evidence. The default audit
mode is best effort; use an acknowledged durability mode when missing events should fail the
operation. Immutable retention and deleted-suffix detection require separate storage and external
anchoring.

See the [architecture](/docs/architecture), [limitations](/docs/limitations), and
[compliance evidence boundaries](/docs/compliance-overview) for the exact boundaries.
