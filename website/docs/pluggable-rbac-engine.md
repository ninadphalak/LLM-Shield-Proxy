# Implementation Reference: Policy Resolution

The MCP tool-policy path separates policy storage from request-time enforcement. A resolver returns
the allowed and blocked tools for a virtual key. The router applies that decision before it sends a
supported tool call upstream.

## Resolver interface

Resolvers implement the asynchronous `BasePolicyResolver` interface:

```python
from abc import ABC, abstractmethod

class BasePolicyResolver(ABC):
    @abstractmethod
    async def resolve_policy(self, virtual_key: str) -> dict:
        """Return allowed and blocked tool scopes for this key."""
        pass
```

## Available resolvers

- **In-memory/YAML:** loads local role mappings and supports periodic reloads.
- **Redis:** reads cached policy from Redis without an external HTTP policy call on a cache hit.
- **OPA:** requests a decision from a configured Open Policy Agent endpoint and keeps a local
  stale-while-revalidate snapshot.
- **Vault:** reads policy from a configured HashiCorp Vault path and keeps a local
  stale-while-revalidate snapshot.
- **Custom:** applications can supply another `BasePolicyResolver` implementation.

These resolvers have different authentication, timeout, cache, revocation, and failure behavior.
Test the selected resolver against the real backend before deployment. A cache hit still includes
serialization and local processing, and a remote refresh adds network work.

## Request behavior

The router resolves policy for the current request. With a non-empty allowlist, an unlisted tool is
denied. An empty allowlist denies every tool by default. Setting
`MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY` changes that behavior to allow every tool not explicitly
blocked and emits a critical startup warning.

Policy changes take effect according to the resolver's reload or cache rules. They do not change a
decision that a request has already resolved.

## Evidence and limits

Tests cover fragmented tool names, resolver decisions, Redis integration, cache invalidation, and
selected failure paths. They do not prove backend availability, complete MCP protocol support, or
correct policy for a particular deployment. The `/v1/mcp` route implements a documented JSON-RPC
subset; it is not a complete MCP Streamable HTTP transport.

See the [MCP Tool Governance guide](/docs/guides/mcp-tool-governance) for configuration and the
[OPA and Vault resolver page](/docs/features/ultra-low-latency-streaming-traffic-engineering/opa-vault-rbac-resolvers)
for cache and failure semantics.
