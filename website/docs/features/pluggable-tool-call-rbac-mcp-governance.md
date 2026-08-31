# Pluggable Tool-Call RBAC (MCP Governance)

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Pluggable Tool-Call Role-Based Access Control (RBAC)** provides strict governance over autonomous AI agents. When an LLM attempts to execute a tool (e.g., calling an API, running a SQL query, or modifying a file via the Model Context Protocol (MCP)), this engine intercepts the execution request mid-stream and validates it against your corporate security policies.

## How It Works
AI Agents are inherently unpredictable. Without strict enforcement, an agent might decide to execute `drop_database_table` instead of `select_users`.

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
- **Overhead:** Extremely lightweight execution enforcing Zero Trust without delaying agent interactions.

## Configuration Flags

| Environment Variable / File | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `policies.yaml` | Define `allowed_tools: ["fetch_data", "read_file"]` per security role. | [View in POLICIES.md](/docs/policies) |
| `ENABLE_AGENT_BREAKER` | Toggles autonomous loop protection and tool interception. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Allowlist behavior:** With a non-empty `allowed_tools` policy on the documented resolver/path, an unlisted tool is denied. The built-in MCP in-memory resolver can be permissive when no allowlist is wired; verify resolver configuration at startup.
* **Graceful Degradation:** When rejecting a tool call, the proxy does not simply return an HTTP 500. It injects a synthetic JSON-RPC error back into the stream, telling the LLM "You do not have permission to use this tool," allowing the agent to dynamically recover and choose a different action.

## FAQ

**Q: Can I integrate this with Open Policy Agent (OPA)?**
A: `BasePolicyResolver` is an extension point. An external OPA integration must define authentication, timeouts, caching, decision schema, revocation, failure mode, and no-PII telemetry before production use.

**Q: Does this secure Model Context Protocol (MCP) servers?**
A: The gateway can enforce configured tool and egress policies on traffic routed through it. It does not prevent prompt injection, detector misses, stolen credentials, direct/bypass connections, or an allowed tool from returning sensitive data; combine it with network and application controls.


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_tool_rbac_and_compliance.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_tool_rbac_and_compliance.py).
