# Tool Catalog Policy Filter (MCP Discovery)

## Overview
The **Tool Catalog Policy Filter** prunes disallowed tools from Model Context Protocol (MCP) `tools/list` discovery responses before they reach the model. The proxy applies tenant-specific Role-Based Access Control (RBAC) to ensure agents only "see" tools they are authorized to execute.

## Architectural Mechanics
* **Policy Filtering:** When the proxy intercepts a `tools/list` response, it checks each tool name against a tenant-specific `frozenset` derived from the active `policies.yaml` role. Unauthorized tools are stripped from the payload.
* **Redis Cache:** To avoid repeatedly querying downstream MCP servers for static catalogs, the proxy caches filtered tool definitions in Redis under a composite hash key (tenant ID + upstream hash).
* **TTL Limits:** Cache Time-To-Live (TTL) is driven by the upstream server's `_meta.ttlMs` field, strictly clamped between 30 and 3,600 seconds (defaulting to 300 seconds).
* **Event-Driven Invalidation:** If the downstream MCP server emits a `notifications/tools/list_changed` event, the proxy immediately invalidates the affected Redis cache entry. Cache staleness can still occur due to network loss, race conditions, or propagation delays across multi-process deployments.

## Practical Effect
This pruner acts as a filter on discovery. By hiding disallowed tools from the agent's context window, it shrinks the catalog size, saving tokens and reducing the attack surface. It does not intelligently rank tools based on user intent; it strictly enforces authorization bounds.
