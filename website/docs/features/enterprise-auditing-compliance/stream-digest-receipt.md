# Signed SSE Stream Digest Receipt

[Back to Features Catalog](/docs/features-overview)

## What it records

`StreamDigestReceipt` maintains a rolling SHA-256 digest over the SSE chunks emitted by the
rehydration pipeline. At stream completion it writes an HMAC-signed audit event containing:

- the session identifier;
- the final rolling digest;
- the number of chunks processed; and
- the emission timestamp.

The emitted event type is `stream_digest_receipt`, and the digest field is
`stream_digest_sha256`. The receipt covers bytes observed by this application-level response-stream path;
it is not a packet capture and does not prove that protected data was absent from every request,
response, log, process, or alternate network route.

## How it works

For each emitted chunk, the implementation computes `SHA256(chunk)` and feeds that digest into a
running SHA-256 state. At completion, the metadata is serialized with sorted keys and signed with
HMAC-SHA-256 using `SHIELD_ENCRYPTION_KEY`.

```mermaid
flowchart LR
    A[Emitted SSE chunk] --> B[SHA-256 of chunk]
    B --> C[Rolling SHA-256 state]
    C --> D[Final digest and chunk count]
    D --> E[HMAC-signed audit event]
```

This is a sequential digest accumulator, not a stored Merkle tree: the implementation retains a
single hash state rather than leaves or authentication paths.

## Evidence boundary

The receipt can support these checks:

- whether supplied receipt metadata was modified after signing;
- whether a separately retained sequence of response chunks reproduces the recorded digest; and
- how many chunks the application says it processed for the session.

It does not establish:

- detector recall or the absence of an undetected sensitive value;
- what the upstream provider received on the request path;
- that another process, log sink, side channel, or route did not transmit data;
- durable or immutable storage of the audit event; or
- the identity of a producer when the HMAC key is shared, ephemeral, or poorly controlled.

Use the configured-upstream conformance check to test declared request values, and use
deployment-level network telemetry when the assurance question concerns actual packets or routes.

## Configuration and key handling

The current implementation requires `SHIELD_ENCRYPTION_KEY` when it emits this receipt. Treat the
key as a secret, load it through the deployment's secret-management mechanism, and document key
identity and rotation. HMAC is symmetric: anyone with the key can generate a valid signature.

The receipt is written through the audit logger. Its delivery and retention properties therefore
depend on the selected audit durability mode and any independently administered retention system.
See [Immutable Retention and External Checkpoint Anchoring](/docs/immutable-retention).

## Performance scope

The path performs one chunk hash, one running-hash update, and final event signing. Its CPU and
allocation cost depend on chunk size and rate and should be measured as part of the selected
service-level workload.

## Related implementation and tests

- `llm_shield_proxy/security/attestation.py`
- `llm_shield_proxy/streaming/streaming.py`
- `tests/test_attestation.py`
