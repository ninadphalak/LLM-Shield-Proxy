# GDPR engineering considerations

LLM-Shield-Proxy provides controls that may support an organization's GDPR program. It does not
determine lawful basis, satisfy data-subject rights, set retention policy, or make a deployment
GDPR compliant. Controllers and processors remain responsible for those decisions and for legal
review.

## Data minimization and erasure

- The proxy can replace detected values before it builds a request for the configured upstream.
  Detection has false positives and false negatives, so test it on the languages and data in
  scope.
- Structured audit events are designed to record categories and decisions instead of prompt and
  response bodies. Verify errors, traces, custom attributes, and downstream log systems
  separately.
- Local and Redis vault mappings expire after a configured TTL. Expiry does not prove that every
  copy was erased from memory, replicas, persistence files, backups, logs, or downstream systems.
- A TTL is not a complete process for Article 17 requests. The organization must identify and
  handle every other system that stores the data.

## Data protection by design

The masking mode controls how a detected value is represented:

- `SYNTHETIC` uses a deterministic, format-aware substitute within the configured mapping scope.
- `STRUCTURAL_TAG` uses a token such as `[PERSON_1]`.
- `SCRUB` uses a fixed marker and creates no rehydration mapping for that value.
- `STATELESS_CRYPTO` encrypts selected values in the payload with AES-256-GCM. Rehydration requires
  an intact token and the correct key and context.

These modes do not improve detector recall. They can also change tokenization, model behavior,
schemas, and response fidelity. Test the selected mode with the actual provider and workload.

## Security of processing

The proxy can contribute to a layered security design through TLS/mTLS configuration, identity
and policy checks, SSRF controls on supported paths, and signed audit records. Each control has a
defined boundary. Local audit files are not WORM storage, and the default best-effort audit mode
can drop events under queue pressure.

See the [architecture](/docs/architecture), [limitations](/docs/limitations), and
[compliance evidence boundaries](/docs/compliance-overview) before using these controls in an
assessment.
