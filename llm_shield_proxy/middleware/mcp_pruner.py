import asyncio
import hashlib
from typing import Any, Callable, Coroutine, Optional, Set

import httpx
import orjson
import redis.asyncio as redis

from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.security.egress_guard import EgressPolicyViolationError, evaluate_url


class MCPDiscoveryPrunerMiddleware:
    """
    Context-Aware Tool Catalog Pruner Middleware.
    Implements SEP-2549 (Progressive Discovery) and SEP-2575 (Stateless Architecture).
    """

    def __init__(self, app: Callable, redis_client: redis.Redis, rbac_resolver: Any):
        self.app = app
        self.redis_client = redis_client
        self.rbac_resolver = rbac_resolver
        self._background_tasks: Set[asyncio.Task] = set()
        # Strict <85 MB limit bounds checking
        self.MAX_PAYLOAD_SIZE = 15 * 1024 * 1024

    def _create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Manage asyncio.Task strong references to prevent Python 3.11+ GC drops."""
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Buffer the request body safely to inspect JSON-RPC method
        receive_buffer = []
        body = bytearray()
        more_body = True

        while more_body:
            message = await receive()
            receive_buffer.append(message)
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                body.extend(chunk)
                more_body = message.get("more_body", False)
                if len(body) > self.MAX_PAYLOAD_SIZE:
                    return await self._send_error(send, 413, "Payload Too Large")
            else:
                more_body = False

        # Re-stream receive callable for downstream consumers
        async def wrapped_receive():
            if receive_buffer:
                return receive_buffer.pop(0)
            return await receive()

        try:
            req_data = orjson.loads(body) if body else {}
            method = req_data.get("method")
            req_id = req_data.get("id") # May be None for notifications
        except Exception:
            method = None
            req_id = None
            req_data = {}

        # Extract context
        tenant_id = "default"
        virtual_key = "BYOK"
        upstream_url = None
        for k, v in scope.get("headers", []):
            k_lower = k.lower()
            if k_lower == b"x-tenant-id":
                tenant_id = v.decode("utf-8")
            elif k_lower == b"x-virtual-key":
                virtual_key = v.decode("utf-8")
            elif k_lower == b"x-upstream-url":
                upstream_url = v.decode("utf-8")

        # Handle Event-Driven Invalidation
        if method == "notifications/tools/list_changed":
            self._create_task(self._invalidate_cache(tenant_id))
            return await self.app(scope, wrapped_receive, send)

        # Handle Discovery & Pruning
        elif method in ("tools/list", "server/discover"):
            if not upstream_url:
                # Missing upstream metadata, fallback to generic pipeline
                return await self.app(scope, wrapped_receive, send)

            return await self._handle_discovery(
                scope, wrapped_receive, send, req_data, tenant_id, virtual_key, upstream_url, req_id
            )

        else:
            return await self.app(scope, wrapped_receive, send)

    async def _invalidate_cache(self, tenant_id: str):
        """Invalidate tenant cache by bumping the policy version."""
        try:
            await self.redis_client.incr(f"mcp:policy_version:{tenant_id}")
        except Exception as e:
            AuditLogger.log_security_event(
                event_type="cache_invalidation_failure",
                severity="WARNING",
                details={"reason": "Redis cache invalidation failed", "error": str(e)},
                virtual_key_id=tenant_id
            )

    async def _handle_discovery(
        self, scope: dict, receive: Callable, send: Callable,
        req_data: dict, tenant_id: str, virtual_key: str,
        upstream_url: str, req_id: Optional[Any]
    ):
        try:
            policy = await self.rbac_resolver.resolve_policy(virtual_key)

            # SSRF / DNS-Rebinding Gate: upstream_url is client-controlled (X-Upstream-URL) and
            # is the actual destination of the outbound request below, so it must clear the same
            # egress firewall used for tool-argument URLs elsewhere in the gateway. Checked before
            # the cache lookup so a forbidden target can never be cached or reach the network.
            try:
                await evaluate_url(upstream_url, policy)
            except EgressPolicyViolationError as exc:
                AuditLogger.log_security_event(
                    event_type="mcp_egress_policy_violation",
                    severity="CRITICAL",
                    details={
                        "reason": exc.reason,
                        "method": "upstream_routing",
                        "blocked_url": exc.url,
                        "blocked_host": exc.host,
                        "resolved_ip": exc.matched_ip,
                        "matched_rule": exc.matched_rule,
                    },
                    virtual_key_id=virtual_key,
                )
                error_payload = orjson.dumps({
                    "type": "https://llm-shield.internal/probs/mcp-egress-forbidden",
                    "title": "MCP Upstream Egress Forbidden",
                    "status": 403,
                    "detail": "SSRF Policy Violation: Target IP/Host forbidden by egress policy",
                    "instance": scope.get("path", "/tools/list")
                })
                return await self._send_error(send, 403, error_payload, is_json=True)

            allowed = set(policy.get("allowed_tools", []))
            blocked = set(policy.get("blocked_tools", []))

            # An absent counter must read as a generation INCR can never return.
            # Redis INCR on a missing key yields 1, so defaulting the read to
            # "1" made a tenant's FIRST tools/list_changed a no-op: the cache
            # key was identical before and after the bump, and a stale catalog
            # kept being served for the rest of the TTL. "0" is outside INCR's
            # range, so every notification changes the key.
            policy_version_bytes = await self.redis_client.get(f"mcp:policy_version:{tenant_id}")
            policy_version = policy_version_bytes.decode("utf-8") if policy_version_bytes else "0"

            # Key Isolation: mcp:tools:{tenant_id}:{hash}
            hash_input = (upstream_url + policy_version).encode("utf-8")
            # Using hashlib for blake2b/blake3 equivalence in standard lib
            key_hash = hashlib.blake2b(hash_input).hexdigest()
            cache_key = f"mcp:tools:{tenant_id}:{key_hash}"

            cached_data = await self.redis_client.get(cache_key)
            if cached_data:
                return await self._send_json_stream(send, cached_data)

            # Managed httpx client inside context manager to prevent connection leaks
            async with httpx.AsyncClient(http2=True) as client:
                async with client.stream("POST", upstream_url, json=req_data, timeout=10.0) as resp:
                    resp.raise_for_status()

                    body_bytes = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body_bytes.extend(chunk)
                        if len(body_bytes) > self.MAX_PAYLOAD_SIZE:
                            raise MemoryError("Upstream payload exceeded strict 85MB limits")

                    upstream_json = orjson.loads(body_bytes)

            # Prune tools (O(1) lookups)
            tools = upstream_json.get("result", {}).get("tools", [])
            pruned_tools = []
            for t in tools:
                t_name = t.get("name")
                if not t_name:
                    continue
                if t_name in blocked:
                    continue
                if allowed and t_name not in allowed:
                    continue
                pruned_tools.append(t)

            # Ensure valid MCP output contracts and preserve exact ID
            if "result" not in upstream_json:
                upstream_json["result"] = {}
            upstream_json["result"]["tools"] = pruned_tools
            if req_id is not None:
                upstream_json["id"] = req_id

            # Dynamic Upstream-Driven TTL
            meta = upstream_json.get("result", {}).get("_meta", {})
            ttl_ms = meta.get("ttlMs", 300000) # Default 300s
            ttl_s = ttl_ms / 1000
            ttl_s = max(30, min(3600, int(ttl_s))) # Clamp min 30s, max 3600s

            response_bytes = orjson.dumps(upstream_json)

            # Background write with strong task reference
            self._create_task(self.redis_client.set(cache_key, response_bytes, ex=ttl_s))

            # Stream downstream safely handling backpressure
            await self._send_json_stream(send, response_bytes)

        except (asyncio.CancelledError, GeneratorExit):
            # Cleanly teardown client socket connections
            raise
        except Exception as e:
            # WORM-Compliant Tracing for Fail-Closed
            AuditLogger.log_security_event(
                event_type="mcp_upstream_failure",
                severity="CRITICAL",
                details={"reason": "MCP Upstream Unavailable", "error": str(e)},
                virtual_key_id=virtual_key
            )

            error_payload = orjson.dumps({
                "type": "https://llm-shield.internal/probs/mcp-upstream-failure",
                "title": "MCP Upstream Unavailable",
                "status": 502,
                "detail": "The upstream MCP server is unreachable or the cache validation failed.",
                "instance": scope.get("path", "/tools/list")
            })
            await self._send_error(send, 502, error_payload, is_json=True)

    async def _send_json_stream(self, send: Callable, body_bytes: bytes, status: int = 200):
        """Yield processed chunks respecting Uvicorn's ASGI send flow control"""
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode("utf-8"))
            ]
        })

        # 64KB chunks to manage ASGI backpressure and memory spikes
        chunk_size = 64 * 1024
        for i in range(0, len(body_bytes), chunk_size):
            chunk = body_bytes[i:i+chunk_size]
            more_body = (i + chunk_size) < len(body_bytes)
            await send({
                "type": "http.response.body",
                "body": chunk,
                "more_body": more_body
            })

    async def _send_error(self, send: Callable, status: int, message: Any, is_json: bool = False):
        if not is_json:
            body = str(message).encode("utf-8")
            content_type = b"text/plain"
        else:
            body = message
            content_type = b"application/problem+json"

        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type),
                (b"content-length", str(len(body)).encode("utf-8"))
            ]
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False
        })
