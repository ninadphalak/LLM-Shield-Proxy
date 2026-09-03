# Pluggable Tool-Call RBAC (MCP Governance)

[⬅️ Back to Features Catalog](/docs/features-overview)

## What it does

This feature checks supported MCP tool calls against the role resolved for the request. A denied
call returns an error before the proxy contacts the tool server.

## How it works

1. **Streaming JSON Interception:** As the LLM streams its decision to use a tool, the proxy uses a bounded-state parser to inspect the `name` or `method` field of the `tool_calls` payload.
2. **Policy Resolution:** The tool name is validated against a pluggable backend (e.g., a Redis Policy Store, HashiCorp Vault, or Open Policy Agent (OPA)).
3. **Denial path:** If the resolved policy does not allow the requested tool, the supported path returns the documented denial response before forwarding that tool call. Parser, resolver, and transport failure modes require separate tests.


```mermaid
flowchart TD
    A[Agent Requests 'exec_sql'] --> B(Tool-Call Lexer)
    B --> C(RBAC Resolver (Redis))
    C -->|Authorized| D[Forward to MCP Server]
    C -->|Unauthorized| E[Reject Tool Execution]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Includes parsing and policy resolution. Measure it with the resolver and traffic
  pattern used in production.

## Configuration Flags

| Environment Variable / File | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `policies.yaml` | Define `allowed_tools: ["fetch_data", "read_file"]` per security role. | [View in POLICIES.md](/docs/policies) |
| `ENABLE_AGENT_BREAKER` | Toggles autonomous loop protection and tool interception. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Allowlist behavior:** With a non-empty `allowed_tools` policy on the documented resolver/path, an unlisted tool is denied. An empty allowlist denies every tool by default. `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY` is the explicit allow-all-except-blocked mode and emits a critical startup warning; verify resolver configuration at startup.
* **Denial response:** A rejected call returns a JSON-RPC permission error instead of contacting
  the tool server. The calling agent decides whether and how to recover.

## FAQ

**Q: Can I integrate this with Open Policy Agent (OPA)?**
A: `BasePolicyResolver` is an extension point. An external OPA integration should define authentication, timeouts, caching, decision schema, revocation, failure mode, and no-PII telemetry before production use.

**Q: Does this secure Model Context Protocol (MCP) servers?**
A: The gateway can enforce configured tool and egress policies on traffic routed through it. It does not prevent prompt injection, detector misses, stolen credentials, direct/bypass connections, or an allowed tool from returning sensitive data; combine it with network and application controls.


## Related Tests
Tests: [`tests/test_tool_rbac_and_compliance.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_tool_rbac_and_compliance.py).
