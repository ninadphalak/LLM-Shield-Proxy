# Stateless AST-Aware Structured-Payload Masking

[Back to Features Catalog](/docs/features-overview)

## Purpose

For supported JSON-RPC 2.0 requests, the proxy parses the payload and walks dictionary and array
values instead of applying replacement directly to serialized JSON text. This preserves valid JSON
syntax and allows the detector to inspect string leaves while leaving structural keys such as
`jsonrpc`, `method`, and `id` unchanged.

Valid JSON is not the same as schema compatibility. Added context fields and array wrapper objects
can be rejected by strict schemas or downstream tools. Test the actual tool definition, provider,
SDK, and parser before deployment.

## Dictionary values

When a dictionary string value is detected, the mutator:

1. replaces the visible value with the configured synthetic or structural substitute;
2. encrypts the original value with session-scoped AES-256-GCM key material; and
3. adds a sibling `_ctx_hash_<property>` field carrying the ciphertext.

The original property remains a string. The response rehydrator removes the sibling and restores
the value only when authenticated decryption succeeds. An input that already contains the reserved
sibling name is rejected rather than overwritten.

## Array values

JSON arrays do not have sibling property names. A detected string element is therefore represented
as an object with `_shield_val` and `_shield_ctx` fields. That changes the element type from string
to object and can be incompatible with a strict array schema. The encrypted context is bound to the
visible substitute so alteration causes authenticated decryption to fail.

## Bounded traversal and execution

The parser applies configured structural-complexity and depth limits and uses an iterative walk. The
CPU-bound parse, detection, and mutation work is offloaded from the ASGI event loop. Limits bound
specific parser state; they do not create a universal latency or process-memory ceiling.

## Key and provider requirements

The JSON-RPC path requires a valid `SHIELD_ENCRYPTION_KEY` and fails the affected request closed
when key material is unavailable. It does not use a built-in fallback key.

Rehydration also depends on the provider returning the encrypted context without destructive
transformation. Schema rewriting communicates the expected sibling fields but does not compel model
behavior. Treat missing, corrupted, or unrecognized context as a failed rehydration case.

## Related implementation and tests

- `llm_shield_proxy/engines/stateless_mutation_engine/ast_mutator.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/streaming_lexer.py`
- `tests/engines/stateless_mutation_engine/`
