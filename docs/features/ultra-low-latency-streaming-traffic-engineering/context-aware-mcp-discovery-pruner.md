# Context-Aware Tool Catalog Pruner (MCP Discovery)

## Overview
Dumping massive tool catalogs into an LLM's context window inflates token costs and directly causes agent hallucinations. To address this and support MCP Progressive Discovery (SEP-2549), the proxy intercepts JSON-RPC `server/discover` and `tools/list` payloads at the network edge.

## Architectural Mechanics
* **O(1) RBAC Pruning:** Tool access is evaluated against tenant-specific `frozenset` collections. Unauthorized tool signatures are silently redacted from the `result.tools` array before the payload reaches the LLM.
* **Multi-Tenant Redis Caching:** Filtered tool definitions are cached using `redis.asyncio` with composite BLAKE3 keys (`mcp:tools:{tenant_id}:{upstream_hash}`).
* **Dynamic TTL Clamping:** The proxy dynamically parses the upstream `_meta.ttlMs` cache metadata. It enforces a safety floor of 30 seconds and a ceiling of 3600 seconds, defaulting to 300 seconds if the upstream server provides no TTL.
* **Event-Driven Invalidation:** The engine intercepts `notifications/tools/list_changed` JSON-RPC packets, automatically triggering an immediate cache flush for the affected tenant to ensure zero-staleness access control.

## Plainspeak
This feature prevents the AI from getting confused or distracted by giving it too many tool options at once.

If an AI connects to a system with thousands of different available tools or databases, seeing that massive list will overwhelm the AI and slow it down. This feature acts like a smart librarian. It figures out exactly what the AI actually needs for its specific task, and trims the list down to only show the most relevant tools, keeping the AI focused and fast.
