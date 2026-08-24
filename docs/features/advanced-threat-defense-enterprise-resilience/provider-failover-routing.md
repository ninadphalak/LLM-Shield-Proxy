# Provider Failover Routing

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
**Provider Failover Routing** ensures zero-downtime availability for critical AI pipelines. When the primary LLM provider (e.g., OpenAI) experiences an outage, network partition, or severe rate-limiting (HTTP 429), the proxy automatically and transparently re-routes the traffic to a pre-authorized secondary mirror (e.g., Azure OpenAI or Anthropic).

## How It Works
High Availability (HA) requires resilience against massive centralized outages.

1. **Error Interception:** The proxy's `httpx` client wraps outbound connections in an exception handler. If it detects a `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`, or a severe `ConnectTimeout`, it intercepts the failure.
2. **Key-Swapping & Rerouting:** The proxy instantly swaps the primary `UPSTREAM_API_KEY` with the `FALLBACK_API_KEY`, updates the base URL, and re-issues the identical JSON payload to the fallback provider.
3. **Seamless Client UX:** The downstream client application receives the final successful stream without ever realizing the primary provider was offline.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart LR
    A[Proxy] -->|HTTP Request| B(Primary Provider)
    B -.->|Timeout / 503| A
    A -->|Swap Key & Reroute| C(Fallback Provider)
    C -.->|HTTP 200 OK| A
    A --> D[Client Application]
```
-->

View diagram on GitHub mobile 📱 -->
![Failover Routing Architecture](../images/provider-failover-routing.svg)

## Performance Profile
- **Execution Speed:** Rerouting logic takes `<1ms`. The total latency delay perceived by the client is solely dependent on the `HTTP_TIMEOUT_SECONDS` configured for the primary attempt.
- **Overhead:** Retries utilize native `asyncio` to prevent thread blocking.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `FALLBACK_BASE_URL` | The URL of the secondary provider. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |
| `FALLBACK_API_KEY` | The API key for the secondary provider. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **No Unapproved Model Downgrades:** The proxy will NEVER silently downgrade a model (e.g., from `gpt-4` to `gpt-3.5-turbo`) to achieve a successful response. It simply reroutes to the identical model name on the fallback URL. It is the operator's responsibility to ensure the fallback URL supports the requested model.
* **4xx Errors are Ignored:** The proxy only fails over on network timeouts and 50x server errors. If OpenAI returns a `400 Bad Request` (e.g., context window exceeded), the proxy passes the error directly to the client, as failing over would simply result in the exact same 400 error on the secondary provider.

## FAQ

**Q: Can I use this to failover between completely different providers, like OpenAI to Anthropic?**
A: Yes. Because the proxy integrates the [Multi-Provider Translators](./multi-provider-translators.md), if you configure an Anthropic fallback URL, the proxy will automatically translate the OpenAI schema into Claude's schema during the failover event.

**Q: How do I test that this works in production?**
A: You can force a failover by intentionally setting `UPSTREAM_BASE_URL` to a blackholed or invalid IP address (like `https://192.0.2.1`). The proxy will timeout on the primary and successfully route to the fallback.


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_enterprise_resiliency.py`](../../../tests/test_enterprise_resiliency.py).
