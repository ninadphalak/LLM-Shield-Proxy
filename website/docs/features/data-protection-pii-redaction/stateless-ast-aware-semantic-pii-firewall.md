# Stateless AST-Aware Structured-Payload Masking

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
For supported JSON-RPC 2.0 requests, the proxy parses the payload and explicitly traverses dictionary and array values instead of applying raw regex replacements across serialized JSON text. This prevents accidental corruption of JSON structure, ensuring that structural keys like `jsonrpc`, `method`, and `id` remain untouched while sensitive string leaves are redacted.

*Note: Outputting syntactically valid JSON does not guarantee strict schema compatibility. Injected context fields or wrapped arrays may be rejected by downstream tools. Always test your specific provider, SDK, and parser.*

## Dictionary Values
When a dictionary string value is flagged for redaction, the mutator:
1. Replaces the visible plaintext with the configured synthetic or structural substitute.
2. Encrypts the original plaintext using session-scoped AES-256-GCM.
3. Injects a sibling field (e.g., `_ctx_hash_<property>`) containing the ciphertext.

Because the original property type remains a string, the response rehydrator can locate the sibling, decrypt the value, and restore the original text transparently. If a payload already contains a reserved `_ctx_hash_` sibling name, the proxy rejects the request to prevent collisions.

## Array Values
JSON arrays cannot contain named sibling properties. Therefore, when a string *element* inside an array requires redaction, the proxy replaces it with an object containing `_shield_val` (the substitute) and `_shield_ctx` (the ciphertext). 

**Warning:** This changes the element's type from a string to an object. If your downstream system enforces strict array schemas (e.g., `Array<string>`), this transformation will cause validation failures.

## Bounded Traversal and Execution
The AST parser applies structural-complexity and depth limits while executing an iterative walk. The CPU-bound parse, detection, and mutation workloads are offloaded from the ASGI event loop to maintain high concurrency. These limits bound parser state but do not impose a universal process-memory ceiling.

## Key and Provider Requirements
The JSON-RPC AST path requires a valid `SHIELD_ENCRYPTION_KEY` and will fail-closed if key material is missing. There is no built-in fallback key.

Rehydration relies entirely on the upstream provider returning the encrypted context field without modification. While dynamic schema rewriting communicates these expected sibling fields to the model, it cannot compel the model to echo them perfectly. Applications must handle missing, corrupted, or hallucinated context gracefully.

## Related Implementation & Tests

- `llm_shield_proxy/engines/stateless_mutation_engine/ast_mutator.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/schema_rewriter.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/streaming_lexer.py`
- `tests/engines/stateless_mutation_engine/`
