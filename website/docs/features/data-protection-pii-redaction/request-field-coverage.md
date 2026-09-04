# Request Field Coverage

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does

Redaction walks every field a request carries, not only the chat fields. The proxy
forwards each field it receives, so any field left unwalked is sent to the provider
as the caller wrote it.

Two groups are handled differently:

- **Known shapes**, walked by structure: `messages` (string and multimodal content,
  participant `name`, `tool_calls` and legacy `function_call` arguments), `prompt`,
  `system` (string or content blocks), `input` (string, string array, or Responses
  API items including `function_call` arguments and `function_call_output` output),
  and `instructions`.
- **Everything else**, walked generically: `metadata`, `user`, `tools`,
  `response_format`, and any provider-specific or unrecognised field.

## What Is Never Rewritten

Some values carry structure rather than prose. Rewriting one does not protect anybody
and can break the request: a tool stops routing, a schema stops validating, a model
name stops resolving.

Built-in protected keys: `model`, `type`, `role`, `enum`, `format`, `object`, `index`,
`finish_reason`, `$ref`, `$schema`, `mime_type`, `encoding_format`.

Add your own with `PAYLOAD_PROTECTED_KEYS` globally, or `payload_skip_keys` per role
in [`policies.yaml`](/docs/policies). JSON is schemaless, so a deployment's own field
names cannot be known in advance; naming them is how you declare them. Sibling fields
are still walked.

## Blobs

Strings longer than `PAYLOAD_MAX_REDACT_STRING_LENGTH` (default 8192), and any `data:`
URI, are forwarded without inspection. A base64 image cannot be matched by a text
detector, and scanning one costs more than the rest of the payload combined.

`UNMAPPED_BLOB_POLICY` decides what happens when such a string appears in a field no
policy claims:

| Value | Behaviour |
| :--- | :--- |
| `skip` | Forwards it silently. |
| `warn` (default) | Forwards it and writes an `UNMAPPED_BLOB_FORWARDED` audit record naming the JSON path and byte size. |
| `block` | Rejects the request with `HTTP 413`, naming the same path in the error. |

Roll out on `warn`, add the paths the audit trail reports to that role's
`payload_skip_keys`, then move to `block`. Both this setting and the ceiling are
overridable per virtual key.

## Cost

Walking text costs roughly 0.1 to 0.7 ms on a chat payload. Raising
`PAYLOAD_MAX_REDACT_STRING_LENGTH` past your largest attachment is the change that
matters: a 1 MB blob in an unclaimed field goes from 0.25 ms to 8.35 ms, because the
walk then scans base64 that can never match.

Measure it on your own payload shapes:

```bash
python benchmarks/payload_walk_latency.py --turns 200 --image-mb 4
```

The `blob cost` column is what raising the ceiling would cost. These are local
microbenchmarks. They exclude network, TLS and model time, so use them to compare
settings against each other rather than as an end-to-end latency figure.

## Limitations

- Only text is redacted. Images, audio and other binary parts pass through untouched.
- Deep walking can be disabled with `ENABLE_DEEP_PAYLOAD_REDACTION=false`, which
  restores the older behaviour and lets unrecognised fields reach the provider
  unredacted.
- A protected or skipped key is not inspected at all. Declaring a field that does
  carry personal data will send that data to the provider.

## Related Implementation & Tests

- `llm_shield_proxy/engines/pii_engine.py` (`redact_payload`, `_deep_redact`)
- `benchmarks/payload_walk_latency.py`
- `tests/test_pii_engine.py`
