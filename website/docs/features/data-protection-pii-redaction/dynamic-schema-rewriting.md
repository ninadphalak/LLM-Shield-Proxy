# Dynamic Schema Rewriting

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does

`DynamicSchemaRewriter` automatically inspects JSON Schema objects within supported request structures (e.g., OpenAI Tool Calling or Structured Outputs). For each string property it finds, it creates a sibling property named `_ctx_hash_<property>` and adds this sibling to the schema's `required` array. 

This injected property holds encrypted cryptographic context used by the stateless rehydration engine to restore redacted values on the return path.

## How It Works

The rewriter deep-copies the original JSON schema and recursively traverses nested objects and arrays. When it identifies a string property that could contain PII, it injects the required context field alongside it.

### Example

**Before (Original Schema):**
```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {"type": "string"}
  },
  "required": ["customer_ssn"]
}
```

**After (Rewritten Schema):**
```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {"type": "string"},
    "_ctx_hash_customer_ssn": {
      "type": "string",
      "description": "Cryptographic context for customer_ssn. Should be provided if customer_ssn is redacted."
    }
  },
  "required": ["customer_ssn", "_ctx_hash_customer_ssn"]
}
```

## Integration Boundaries & Limitations

The JSON Schema `required` entry describes the expected output shape, but it **cannot force** an LLM, provider, or downstream parser to perfectly echo the injected field. 

You must test your specific integration because:
- Some providers reject unknown fields.
- Some models fail to return fields even when marked as `required`.
- Strict downstream validators might reject the injected `_ctx_hash` properties.

If the encrypted context field is missing, altered, or hallucinated by the model, stateless rehydration will fail. Your application must handle these failures gracefully rather than assuming the original value can always be recovered.

## Related Implementation & Tests

- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `tests/engines/stateless_mutation_engine/test_ast_mutator.py`
- `tests/test_audit_remediation.py`
