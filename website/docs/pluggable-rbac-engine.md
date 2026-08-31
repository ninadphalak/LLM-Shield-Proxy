# Implementation Reference: Pluggable Policy Resolution Engine

## Architectural Overview
To satisfy enterprise constraints, the Tool-Call RBAC Engine in LLM-Shield-Proxy decouples the **Control Plane** (where policies are stored) from the **Data Plane** (where policies are enforced).

Because policy resolution sits on a streaming request path, its remote operations must be asynchronous, bounded, and fail closed.

## 1. The Abstract Base Class (ABC)
The core interface is defined by `BasePolicyResolver`. All enterprise adapters must inherit from this and implement the async resolution method.

```python
from abc import ABC, abstractmethod

class BasePolicyResolver(ABC):
    @abstractmethod
    async def resolve_policy(self, virtual_key: str) -> dict:
        """
        Must return a dictionary mapping allowed and blocked tool scopes:
        {"allowed_tools": [...], "blocked_tools": [...]}
        """
        pass

## 2. High-Speed Cache: RedisPolicyResolver

The Redis-backed implementation can resolve cached policy without an external HTTP call on that cache hit. Redis access, serialization, cache misses, and resolver behavior still have measurable cost.

* **Implementation Detail:** Uses asyncio Redis clients (`redis.asyncio`) and `orjson`; measure process RSS and resolver latency under the intended concurrency.
* **Fail-closed logic:** If `shield:rbac:{virtual_key}` is missing or expired, the Redis resolver returns no allowed tools and the supported tool-call path rejects the request before its configured upstream action.

## 3. Enterprise Infrastructure Adapters (Stubs)

To integrate with global Zero-Trust architectures, the engine includes standard adapters mapped to the `BasePolicyResolver`:
* **OPAPolicyResolver:** Resolves scopes against Cloud Native Computing Foundation (CNCF) Rego policies.
* **VaultPolicyResolver:** Resolves scopes dynamically from HashiCorp Vault KV engines.
*(Note: Active external network bindings for OPA/Vault are scheduled for v1.2).*

## 4. Dependency Injection & Dynamic Stream Evaluation

The `RBACValidator` accepts the policy resolver via constructor injection. This prevents tight coupling and allows the execution plane to dynamically adapt to the deployment environment.

Policy resolution occurs at the documented per-stream boundary. A policy change affects requests according to resolver caching and when each stream resolves policy; it does not retroactively change an already resolved decision.

## 5. Security & Validation (Test Strategy)

The pluggable architecture is validated using `unittest.mock.AsyncMock` (or `fakeredis`).
* **Vector:** A mock Redis client is seeded with a blocked tool (`exec_sql`).
* **Execution:** A fragmented byte-stream representing the JSON tool call is fed into the `StreamingToolParser`.
* **Assertion:** The parser yields the key, the `RBACValidator` invokes the `RedisPolicyResolver`, and a `ToolAccessForbiddenException` is raised at the tested boundary. Separate payload-size, line-size, timeout, and load tests cover resource-exhaustion risks.
