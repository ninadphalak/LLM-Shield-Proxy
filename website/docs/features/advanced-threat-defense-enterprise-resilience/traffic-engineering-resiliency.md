# Traffic Engineering & Resiliency

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Traffic Engineering & Resiliency** encompasses a suite of low-level networking safeguards designed to protect the LLM-Shield-Proxy against massive traffic spikes, volumetric DDoS attacks, and backend LLM bottlenecks. It ensures the proxy remains highly available (99.99%) even when subjected to adversarial loads.

## How It Works
The proxy leverages high-performance Python `asyncio` primitives combined with Redis Lua scripting to enforce strict traffic discipline.

1. **Token-Bucket Rate Limiting:** The proxy executes a Lua script (`evalsha`) on the Redis cluster for every incoming request. It enforces a strict RPM (Requests Per Minute) limit mapped to the client's Virtual Key.
2. **Concurrency Shedding:** If the proxy detects that the `asyncio` event loop is experiencing severe latency (e.g., event loop lag > 100ms), it activates load-shedding, instantly returning `503 Service Unavailable` to new connections to protect active streams.
3. **Timeout Disciplines:** Every outbound HTTP request is wrapped in a strict `httpx.Timeout` structure, enforcing absolute limits on `connect`, `read`, and `write` operations.


```mermaid
flowchart TD
    A[Massive Traffic Spike] --> B(Redis Lua Token-Bucket)
    B -->|Exceeds RPM| C[HTTP 429 Too Many Requests]
    B -->|Under RPM| D(Check Event Loop Lag)
    D -->|>100ms| E[HTTP 503 Load Shedding]
    D -->|&lt;100ms| F[Process Request]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Execution Speed:** Lua script evaluation takes `&lt;1ms`. Event loop monitoring is instantaneous (`O(1)`).
- **Overhead:** Extremely lightweight. Rejecting a request via `429` or `503` bypasses the entire JSON lexer and PII cascade, costing nearly zero CPU cycles.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `DEFAULT_RATE_LIMIT_RPM` | Base Requests Per Minute allowed per Virtual Key (default 6000). | [View in deployment.md](/docs/deployment) |
| `MAX_CONCURRENT_STREAMS` | Hard cap on active HTTP/2 SSE streams per pod. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Burst Allowances:** The Token-Bucket algorithm natively supports bursts. A client can execute 200 requests in a single second, provided they do not exceed the 6,000 RPM average over the rolling window, seamlessly supporting map-reduce style agent workloads.
* **Upstream Timeout Segregation:** `connect` timeouts are kept aggressively short (e.g., 2 seconds) because TCP handshakes should be instant. `read` timeouts are set significantly longer (e.g., 60 seconds) to accommodate slow-generating LLM responses.

## FAQ

**Q: Can I set different rate limits for different departments?**
A: Yes! Using `policies.yaml`, you can assign `rate_limit_rpm: 10000` to a `role_data_science`, while limiting `role_interns` to `100`.

**Q: What happens if Redis goes down? Do all requests get rate-limited?**
A: No. The proxy fails *open* for rate limiting. If the Redis cluster is unreachable, the proxy logs a severe warning but allows the traffic to flow through, prioritizing availability over strict rate enforcement (unless configured to fail-closed via security policies).


## Plainspeak
This feature acts as a smart speed limit for incoming requests to prevent your infrastructure from being overwhelmed.

If a massive spike of thousands of users suddenly tries to use the AI all at the same time, it could crash the entire system. This feature uses a specialized "token bucket" system to enforce a strict speed limit (like allowing a maximum of 6000 requests per minute). Anyone who exceeds this limit is gently told to slow down, ensuring the system stays online for everyone else.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_enterprise_resiliency.py`](https://github.com/YOUR_ORG/LLM-Shield-Proxy/blob/main/tests/test_enterprise_resiliency.py).
