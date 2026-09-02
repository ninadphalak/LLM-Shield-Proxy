# Dynamic MCP Tool Schema Rewriting

[Back to Features Catalog](/docs/features-overview)

This feature uses the shared
[Dynamic Schema Rewriting](/docs/features/data-protection-pii-redaction/dynamic-schema-rewriting)
utility to discover JSON Schema objects inside supported tool-definition requests.

For each string property, the current implementation adds a `_ctx_hash_<property>` sibling and
marks it as required. The field is intended to carry encrypted context for stateless rehydration.

## Compatibility limits

- A required field communicates an expectation but does not compel a model or provider to echo it.
- Strict schemas can reject injected fields, especially when `additionalProperties` is false.
- SDKs and tool frameworks can remove unknown fields before the proxy receives the call.
- Provider adapters use different request and response shapes; compatibility must be demonstrated
  by an integration test for each provider/model/parser combination.
- Backends that receive the context field directly may need an explicit schema update or a proxy
  rehydration step before validation.

LLM-Shield-Proxy tests recursive schema discovery and confirms that the input object is not changed
in place. It does not claim compatibility with every OpenAI, Anthropic, Gemini, MCP, or Pydantic
schema.

## Related implementation and tests

- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `tests/engines/stateless_mutation_engine/test_ast_mutator.py`
- `tests/test_audit_remediation.py`
