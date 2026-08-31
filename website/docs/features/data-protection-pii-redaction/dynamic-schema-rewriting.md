# Dynamic Schema Rewriting

[Back to Features Catalog](/docs/features-overview)

## Purpose

`DynamicSchemaRewriter` discovers JSON Schema objects in supported request structures. For each
string property it adds a sibling property named `_ctx_hash_<property>` and adds that sibling to
the schema's `required` array. The sibling is intended to carry encrypted context used by the
stateless structured-payload path.

The utility deep-copies its input and recursively handles nested object and array schemas, including
schemas embedded in a larger tool-definition request.

## Example

Before:

```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {"type": "string"}
  },
  "required": ["customer_ssn"]
}
```

After:

```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {"type": "string"},
    "_ctx_hash_customer_ssn": {
      "type": "string",
      "description": "Cryptographic context for customer_ssn. Must be provided if customer_ssn is redacted."
    }
  },
  "required": ["customer_ssn", "_ctx_hash_customer_ssn"]
}
```

## Integration boundary

A JSON Schema `required` entry describes expected output. It does not force an LLM, provider,
decoder, or tool client to preserve or echo a field. Some providers reject unknown fields, some
models omit required fields, and strict downstream validators may disallow injected properties.

Before relying on stateless rehydration, test the exact combination of:

- provider and model;
- structured-output or tool-call mode;
- SDK and response parser;
- schema settings such as `additionalProperties`; and
- streaming and non-streaming response paths.

If the encrypted context is missing or altered, rehydration cannot recover the original value. The
application must handle that condition without forwarding unverified plaintext or assuming success.

## Related implementation and tests

- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `tests/engines/stateless_mutation_engine/test_ast_mutator.py`
- `tests/test_audit_remediation.py`
