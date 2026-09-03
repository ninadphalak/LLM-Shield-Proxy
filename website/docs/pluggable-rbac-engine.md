# Pluggable RBAC Engine

LLM-Shield-Proxy separates MCP tool policy storage from request-time enforcement. A resolver interface determines the allowed and blocked tools for a given virtual key, and the router applies that decision before forwarding the tool call upstream.

## Resolver Interface

All resolvers implement the `BasePolicyResolver` asynchronous interface:

```python
from abc import ABC, abstractmethod

class BasePolicyResolver(ABC):
    @abstractmethod
    async def resolve_policy(self, virtual_key: str) -> dict:
        """Return allowed and blocked tool scopes for this key."""
        pass
```

## Available Resolvers

- **In-memory/YAML:** Loads role mappings from a local file and polls for updates.
- **Redis:** Reads cached policies from a Redis cluster.
- **OPA:** Requests a decision from an Open Policy Agent (OPA) endpoint, maintaining a local stale-while-revalidate cache.
- **Vault:** Reads policies from HashiCorp Vault, maintaining a local stale-while-revalidate cache.
- **Custom:** You can implement and inject your own `BasePolicyResolver`.

## Request Behavior

The router resolves the policy per request.
- **Allowlist defined:** Any unlisted tool is denied.
- **Empty allowlist:** All tools are denied by default (`DENY_ALL`).
- **Blocklist-only mode:** Setting `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY` allows any tool not explicitly blocked. *Note: This emits a critical startup warning as it violates secure-by-default principles.*
