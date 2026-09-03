# Request-Scoped Dynamic Override Engine

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Request-Scoped Dynamic Override Engine** allows individual HTTP requests to override specific, authorized settings (like the PII masking mode) without altering the global state of the proxy and without requiring every internal Python function to accept those settings as arguments.

## How It Works
The engine uses Python's `contextvars` to isolate configurations per request concurrently.

1. **Context Initialization:** When a request arrives, the proxy extracts authorized overrides from HTTP headers (e.g., `X-Shield-Masking-Mode`) or tenant policies.
2. **Contextvars Injection:** These overrides are injected into a `contextvars.ContextVar` at the top of the ASGI request lifecycle.
3. **Retrieval:** Deep internal layers (like the redaction engine) read the `ContextVar` directly. Because `contextvars` are natively isolated per `asyncio` task, one request cannot accidentally read another concurrent request's overrides.

```mermaid
flowchart TD
    A[Request Header: X-Shield-Mode=SCRUB] --> B(Middleware contextvar setter)
    B --> C[Layer 1: Routing]
    C --> D[Layer 2: Parsing]
    D --> E(Layer 3: Redaction Engine)
    E -->|O(1) contextvar getter| F[Returns SCRUB]
```

## Performance Profile
- **Overhead:** Setting and retrieving `ContextVar` values is highly optimized in Python and avoids the latency of external lookups (like Redis or database queries).

## Implementation Details & Edge Cases
* **Allowlist Enforcement:** The proxy strictly enforces which settings can be overridden. Security-sensitive settings (like API keys, routing destinations, or bypass flags) cannot be overridden via client headers.

## FAQ

**Q: Can a client use this to override their rate limits?**
A: No. Rate limits, upstream endpoints, and audit configurations are explicitly excluded from client-side overrides to prevent abuse.

## Practical Effect
This feature provides a fast, thread-safe mechanism to apply custom tenant configurations (like scrubbing vs. synthetic PII redaction) on a per-request basis without complicating the internal code architecture.

## Related Tests
Tests: [`tests/test_policy_engine.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_policy_engine.py).
