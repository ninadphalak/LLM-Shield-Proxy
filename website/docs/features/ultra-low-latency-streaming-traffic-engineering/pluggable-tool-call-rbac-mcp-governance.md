# Pluggable Tool-Call RBAC (MCP Governance)

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Pluggable Tool-Call RBAC (MCP Governance)** evaluates the method subset supported by `/v1/mcp` against a configured policy resolver. In-memory, Redis, OPA, and Vault resolver classes exist, but resolver defaults, wiring, and failure behavior determine the effective policy.

## How It Works
This feature evaluates supported tool calls against the configured resolver before forwarding. Security and latency depend on resolver defaults, cache state, network deadlines, and failure behavior.
1. **Initialization:** Configured during startup via `policies.yaml` or `.env`.
2. **Execution:** Uses asynchronous interfaces on documented paths. Throughput depends on parsing, resolver/cache state, external services, audit settings, concurrency, and payloads.
3. **Completion:** Mutates or validates the payload safely before egress to the upstream LLM provider.


```mermaid
graph LR
    A[Input Stream] --> B(Pluggable Tool-Call RBAC (MCP Governance))
    B --> C[Sanitized Output]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Depends on JSON parsing, resolver/cache state, backend latency, audit work, and concurrency. Measure the configured path.

## Configuration Flags
The `/v1/mcp` route is registered by the application. Policy resolver selection and upstream routing are configured separately.

| Environment Variable / Config | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `OPA_URL` | Selects the OPA resolver in the default dependency factory when configured. | [View in deployment.md](/docs/deployment) |
| `REDIS_URL` | Enables the Redis-backed resolver/store paths where the application selects them. | [View in deployment.md](/docs/deployment) |
| `X-Shield-Upstream-URL` / `UPSTREAM_MCP_BASE_URL` | Selects the upstream for the scoped MCP gateway; the environment fallback is read directly by the router. | [View in the governance guide](/docs/guides/mcp-tool-governance) |

## Critical Logic & Edge Cases
* **Streaming boundary tests:** Exercises supported split-token fixtures with the configured lookahead; other encodings, envelopes, and tokens require additional cases.
* **Default-policy warning:** Without OPA, Vault, Redis policy data, or an application override, the current in-memory resolver is permissive except for explicit blocks. Do not describe the route as fail-closed until the deployed resolver and outage behavior demonstrate it.

## FAQ
**Q: Is `/v1/mcp` a complete MCP Streamable HTTP implementation?**
A: No. It supports a documented JSON-RPC subset and does not implement initialization, capability negotiation, sessions, GET/SSE, or every MCP method.

**Q: Where can I see the audit logs for this feature?**
A: Instrumented decisions can emit configured audit, OTel, or OSCAL metadata. Delivery and completeness depend on settings and downstream systems, and the artifacts do not establish SOC 2 compliance.


## Plainspeak
This feature is a policy checkpoint for supported tool calls on one scoped gateway route.

For supported calls, the route resolves caller policy before forwarding. Its effectiveness depends on routing all relevant calls through the route and configuring a resolver that denies the intended operations and fails as required.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_proxy.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_proxy.py).
