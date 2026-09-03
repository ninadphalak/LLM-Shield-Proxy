# Dynamic MCP Tool Schema Rewriting

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
This feature uses the proxy's Dynamic Schema Rewriting utility to automatically inject required metadata fields into JSON Schema objects defined within Model Context Protocol (MCP) tool requests. It is primarily used to embed encrypted context for stateless PII rehydration.

## How It Works
When the proxy receives an MCP tool definition request, it scans the payload for JSON Schema objects.

For every string property it finds in the schema, the proxy injects a new sibling property named `_ctx_hash_<property>` and marks this new field as required. When the upstream LLM returns a tool call, this field is intended to carry the encrypted mapping context needed to rehydrate the redacted string.

## Implementation Details & Edge Cases

* **Strict Schemas:** If a tool schema is defined with `additionalProperties: false`, injecting a new required field may cause upstream validation errors if the provider strictly enforces the original schema shape.
* **SDK Stripping:** Some client SDKs or tool execution frameworks might silently drop "unknown" fields (like `_ctx_hash_<property>`) before the proxy even sees them, breaking the stateless rehydration loop.
* **Provider Compatibility:** A required field in a schema expresses an expectation, but it does not force the LLM to successfully populate it. Different providers handle schema compliance differently. You must run integration tests for your specific combination of Provider, Model, and Client SDK.
* **Backend Validation:** The downstream backend that eventually executes the tool call will receive this `_ctx_hash_<property>` field. The backend must either ignore this field or the proxy must strip it out before final execution.

## Practical Effect
This feature modifies tool schemas on the fly to support stateless PII rehydration, but it heavily depends on the upstream LLM and client SDK correctly handling and echoing injected schema fields without breaking validation.

## Related Tests
Tests:
- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `tests/engines/stateless_mutation_engine/test_ast_mutator.py`
- `tests/test_audit_remediation.py`
