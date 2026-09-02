# Tool Catalog Policy Filter (MCP Discovery)

## Overview
The pruner removes tools that the resolved policy does not allow from supported `tools/list`
responses. Its middleware also recognizes `server/discover`, but the shipped `/v1/mcp` route does
not expose that method. The pruner does not infer which allowed tools are relevant to the user's
current task.

## Architectural Mechanics
* **Policy filtering:** The middleware checks tool names against tenant-specific `frozenset`
  collections and removes disallowed entries from `result.tools`.
* **Redis cache:** Filtered tool definitions can be cached with `redis.asyncio` under a composite
  tenant and upstream hash key.
* **TTL limits:** Upstream `_meta.ttlMs` values are clamped between 30 and 3,600 seconds. The
  default is 300 seconds.
* **Event-Driven Invalidation:** A handled `notifications/tools/list_changed` message invalidates the affected cache entry. Delivery loss, races, TTL, multi-process caches, and resolver propagation can still produce stale observations.

## Practical effect
The pruner hides tools that policy denies before an allowed `tools/list` result reaches the model.
This can reduce catalog size. It does not rank tools, understand the task, or prove that a smaller
catalog improves model behavior.
