# Pluggable Tool-Call RBAC (MCP Governance)

[⬅️ Back to Features Catalog](../../FEATURES.md)

## What It Does
**Pluggable Tool-Call Role-Based Access Control (RBAC)** provides strict governance over autonomous AI agents. When an LLM attempts to execute a tool (e.g., calling an API, running a SQL query, or modifying a file via the Model Context Protocol (MCP)), this engine intercepts the execution request mid-stream and validates it against your corporate security policies.

## How It Works
AI Agents are inherently unpredictable. Without strict enforcement, an agent might decide to execute `drop_database_table` instead of `select_users`.

1. **Streaming JSON Interception:** As the LLM streams its decision to use a tool, the proxy uses a Zero-Allocation Pushdown Automaton to inspect the `name` or `method` field of the `tool_calls` payload.
2. **Policy Resolution:** The tool name is validated against a pluggable backend (e.g., a Redis Policy Store, HashiCorp Vault, or Open Policy Agent (OPA)).
3. **Instant Circuit Breaking:** If the Virtual Key associated with the request is not authorized to execute the specific tool, the proxy deterministically rejects the tool call, synthesizing a safe failure response to the agent and dropping the upstream socket.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart TD
    A[Agent Requests 'exec_sql'] --> B(Tool-Call Lexer)
    B --> C{RBAC Resolver (Redis)}
    C -->|Authorized| D[Forward to MCP Server]
    C -->|Unauthorized| E[Reject Tool Execution]
```
-->

View diagram on GitHub mobile 📱 -->
![Tool-Call RBAC Architecture](./images/pluggable-tool-call-rbac-mcp-governance-.svg)

## Performance Profile
- **Execution Speed:** Tool extraction and local policy validation occurs in `<1.0 µs`.
- **Overhead:** Extremely lightweight execution enforcing Zero Trust without delaying agent interactions.

## Configuration Flags

| Environment Variable / File | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `policies.yaml` | Define `allowed_tools: ["fetch_data", "read_file"]` per security role. | [View in POLICIES.md](../../POLICIES.md) |
| `ENABLE_AGENT_BREAKER` | Toggles autonomous loop protection and tool interception. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Fail-Closed Default:** If a tool is not explicitly listed in a user's `allowed_tools` array, the execution is universally blocked.
* **Graceful Degradation:** When rejecting a tool call, the proxy does not simply return an HTTP 500. It injects a synthetic JSON-RPC error back into the stream, telling the LLM "You do not have permission to use this tool," allowing the agent to dynamically recover and choose a different action.

## FAQ

**Q: Can I integrate this with Open Policy Agent (OPA)?**
A: The RBAC engine is explicitly designed as a `BasePolicyResolver` interface. While Redis and YAML are provided out of the box, you can seamlessly extend the interface to ping an external OPA endpoint for highly dynamic, context-aware decisions.

**Q: Does this secure Model Context Protocol (MCP) servers?**
A: Absolutely. By deploying the proxy between your LLM and your MCP servers, you enforce a strict authorization boundary, preventing malicious prompts from leveraging your agent's MCP permissions to exfiltrate data.
