# Traffic Engineering & Resiliency

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Traffic Engineering & Resiliency** combines rate limits, timeouts, retries, and shutdown behavior for selected overload and dependency-failure cases. The project does not publish a universal availability SLO; capacity and availability must be measured for the deployed topology.

## How It Works
The proxy leverages high-performance Python `asyncio` primitives combined with Redis Lua scripting to enforce strict traffic discipline.

1. **Token-Bucket Rate Limiting:** The proxy executes a Lua script (`evalsha`) on the Redis cluster for every incoming request. It enforces a strict RPM (Requests Per Minute) limit mapped to the client's Virtual Key.
2. **Concurrency shedding:** When the measured event-loop lag crosses the configured threshold, supported new-request paths can return `503`. Detection and routing are not instantaneous and do not guarantee active-stream health.
3. **Timeout disciplines:** The shared HTTP client applies configured connect, read, write, and pool timeouts. Total wall-clock duration can also include retries, queueing, DNS, streaming, and application work.


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
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Extremely lightweight. Rejecting a request via `429` or `503` bypasses the entire JSON lexer and PII cascade, costing nearly zero CPU cycles.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `RATE_LIMIT_RPM` | Requests per minute per virtual key (default 6000 when enabled). | [View in deployment.md](/docs/deployment) |
| `RATE_LIMIT_BURST` | Token-bucket burst capacity (default 200 when enabled). | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Burst allowances:** Token-bucket configuration can permit bursts up to its capacity. Exact admission behavior depends on refill rate, clock, Redis availability, concurrency, and tenant-key construction.
* **Upstream timeout segregation:** Connect and read timeouts can be tuned independently. Choose values from measured network and provider behavior; TCP/TLS handshakes are not instantaneous.

## FAQ

**Q: Can I set different rate limits for different departments?**
A: Yes! Using `policies.yaml`, you can assign `rate_limit_rpm: 10000` to a `role_data_science`, while limiting `role_interns` to `100`.

**Q: What happens if Redis goes down? Do all requests get rate-limited?**
A: No. The proxy fails *open* for rate limiting. If the Redis cluster is unreachable, the proxy logs a severe warning but allows the traffic to flow through, prioritizing availability over strict rate enforcement (unless configured to fail-closed via security policies).


## Plainspeak
This feature acts as a smart speed limit for incoming requests to prevent your infrastructure from being overwhelmed.

If a massive spike of thousands of users suddenly tries to use the AI all at the same time, it could crash the entire system. This feature uses a specialized "token bucket" system to enforce a strict speed limit (like allowing a maximum of 6000 requests per minute). Anyone who exceeds this limit is gently told to slow down, ensuring the system stays online for everyone else.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_enterprise_resiliency.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_enterprise_resiliency.py).
