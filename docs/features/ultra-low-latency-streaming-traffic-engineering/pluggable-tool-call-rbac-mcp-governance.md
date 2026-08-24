# Pluggable Tool-Call RBAC (MCP Governance)

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
The **Pluggable Tool-Call RBAC (MCP Governance)** is a critical component of the LLM-Shield-Proxy. 
Intercepts autonomous JSON-RPC tool executions and enforces strict logical access controls against your existing Redis infrastructure. *(OPA and HashiCorp Vault resolvers planned for v1.2)*

## How It Works
This feature integrates directly into the zero-egress VPC architecture to ensure secure and ultra-low latency processing. 
1. **Initialization:** Configured during startup via `policies.yaml` or `.env`.
2. **Execution:** Operates asynchronously within the data plane, guaranteeing high throughput.
3. **Completion:** Mutates or validates the payload safely before egress to the upstream LLM provider.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
graph LR
    A[Input Stream] --> B(Pluggable Tool-Call RBAC (MCP Governance))
    B --> C[Sanitized Output]
```
-->

View diagram on GitHub mobile 📱 -->
![System Architecture](../images/pluggable-tool-call-rbac-mcp-governance.svg)

## Performance Profile
- **Execution Speed:** Designed for microsecond-level latency impact.
- **Overhead:** Highly concurrent execution without saturating the Python GIL.

## Configuration Flags
The engine operates automatically but can be tuned via deployment flags.

| Environment Variable / Config | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_PLUGGABLE_TOOL_CALL_RBAC_MCP_GOVERNANCE` | Toggles this functionality. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Streaming Integrity:** Seamlessly handles split token chunks in real-time.
* **Security Stance:** Enforces a Zero-Trust, fail-closed default architecture.

## FAQ
**Q: Does this break real-time streaming?**
A: No, the proxy is engineered to reconstruct and redact payloads on the fly without breaking SSE connections.

**Q: Where can I see the audit logs for this feature?**
A: All decisions are exported via the Universal Decision Trace Exporter (OTel / OSCAL) for SOC 2 compliance.


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_proxy.py`](../../../tests/test_proxy.py).
