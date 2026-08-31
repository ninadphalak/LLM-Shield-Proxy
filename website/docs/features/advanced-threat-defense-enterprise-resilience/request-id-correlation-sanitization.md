# Request-ID Correlation & Sanitization

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Request-ID Correlation & Sanitization** normalizes a correlation identifier for supported request, response, and telemetry paths. End-to-end traceability depends on every participating component preserving and indexing the identifier.

## How It Works
A consistent identifier can make multi-component debugging substantially easier when each component records it.

1. **Ingress Extraction:** When a request arrives, the proxy looks for the `X-Request-ID` or `X-Correlation-ID` HTTP headers.
2. **Regex Sanitization:** To prevent HTTP Header Injection attacks (CWE-113), the supplied ID is validated against a strict `^[a-zA-Z0-9-]{10,40}$` regex. If it contains invalid characters (like newlines or scripts), it is discarded.
3. **UUID4 Generation:** If no valid header is found, the proxy automatically generates a cryptographically secure `UUID4`.
4. **Supported propagation:** The normalized ID is stored on `request.state`, returned in the `X-Request-ID` response header, and passed explicitly into supported audit/error events. The current catch-all path does not promise automatic propagation to every log line, trace span, or upstream request.


```mermaid
flowchart LR
    A[Client Header: X-Request-ID] --> B(Regex Sanitizer)
    B -->|Invalid / Missing| C(Generate UUID4)
    B -->|Valid| D[contextvars Injection]
    C --> D
    D --> E[Upstream HTTP Header]
    D --> F[Audit Logs & OTel Spans]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** `contextvars` execution is thread-safe and eliminates the need to pass `request_id` variables deeply through the call stack.

## Configuration Flags
This feature operates fundamentally at the middleware layer and requires no explicit configuration flags to activate.

## Critical Logic & Edge Cases
* **Response echo:** The supported proxy response path adds the finalized `X-Request-ID`. Gateways and clients can remove or replace headers, so verify the complete path.
* **Log-injection reduction:** Sanitizing control characters reduces request-ID log injection. Logging format, downstream parsing, and every other untrusted field still require defensive handling.

## FAQ

**Q: Can I use standard W3C `traceparent` headers instead?**
A: `X-Request-ID` supports application correlation, while W3C `traceparent` supports distributed tracing. They have different trust and propagation rules; test both across the selected gateways and collectors. See [Asynchronous OpenTelemetry Tracing](/docs/features/enterprise-auditing-compliance/zero-overhead-opentelemetry-otel-tracing).

**Q: Is the Request ID passed to OpenAI/Anthropic?**
A: Yes. It is appended as a custom header on the upstream HTTP/2 connection. While OpenAI doesn't natively expose this in their standard dashboards, it provides critical proof during enterprise support tickets when correlating proxy traffic with upstream provider logs.


## Plainspeak
This feature acts as a tracking number system for your data.

The proxy assigns or normalizes a tracking identifier that participating services can record. It is a correlation aid, not proof of every hop or event.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_security_hardening.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_security_hardening.py).
