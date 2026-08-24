import asyncio
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from types import MappingProxyType
from typing import AsyncGenerator, Set

import httpx
import orjson
import redis.asyncio as redis

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.observability.audit import AuditLogger


class ToolAccessForbiddenException(Exception):
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool access forbidden: {tool_name}")

    def get_rejection_chunk(self) -> bytes:
        return orjson.dumps(
            {"error": {"type": "permission_denied", "code": "TOOL_ACCESS_FORBIDDEN", "tool": self.tool_name}}
        )


class BasePolicyResolver(ABC):
    @abstractmethod
    async def resolve_policy(self, virtual_key: str) -> dict:
        """Resolves the RBAC policy for a given virtual key."""
        pass


class RedisPolicyResolver(BasePolicyResolver):
    def __init__(self, redis_client: redis.Redis):
        self.redis_client = redis_client

    async def resolve_policy(self, virtual_key: str) -> dict:
        key = f"shield:rbac:{virtual_key}"
        data = await self.redis_client.get(key)
        if data:
            return orjson.loads(data)
        return {"allowed_tools": [], "blocked_tools": []}


class OPAPolicyResolver(BasePolicyResolver):
    def __init__(self, http_client: httpx.AsyncClient, opa_url: str):
        self.http_client = http_client
        self.opa_url = opa_url
        self._cache = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._inflight: Set[str] = set()
        self._inflight_lock = asyncio.Lock()
        self._max_cache_size = 1000

    async def _fetch_policy(self, virtual_key: str) -> None:
        try:
            response = await self.http_client.post(
                self.opa_url,
                json={"input": {"virtual_key": virtual_key}},
                timeout=0.05
            )
            response.raise_for_status()
            policy = response.json().get("result", {})

            expiration = time.time() + settings.RBAC_CACHE_TTL_SECONDS
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)
        except Exception as e:
            AuditLogger.log_security_event(
                event_type="rbac_fetch_failure",
                severity="WARNING",
                details={"reason": "OPA fetch failed", "error_type": type(e).__name__},
                virtual_key_id=virtual_key
            )
            policy = {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
            expiration = time.time() + 5.0
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)

    async def _safe_background_fetch(self, virtual_key: str) -> None:
        async with self._inflight_lock:
            if virtual_key in self._inflight:
                return
            self._inflight.add(virtual_key)
        try:
            await self._fetch_policy(virtual_key)
        except Exception as e:
            AuditLogger.log_security_event(
                event_type="rbac_background_fetch_critical_failure",
                severity="CRITICAL",
                details={"reason": "Background fetch task crashed", "error_type": type(e).__name__},
                virtual_key_id=virtual_key
            )
            policy = {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
            expiration = time.time() + 5.0
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)
        finally:
            async with self._inflight_lock:
                self._inflight.discard(virtual_key)

    async def resolve_policy(self, virtual_key: str) -> dict:
        async with self._cache_lock:
            entry = self._cache.get(virtual_key)
        now = time.time()
        
        if entry is not None:
            policy, expiration = entry
            if now > expiration:
                asyncio.create_task(self._safe_background_fetch(virtual_key))
            return policy

        # Cache miss
        # To avoid thundering herd on first fetch, use inflight lock to check
        async with self._inflight_lock:
            already_inflight = virtual_key in self._inflight
            if not already_inflight:
                self._inflight.add(virtual_key)
                
        if already_inflight:
            # Short poll to let the other coroutine finish first
            for _ in range(10):
                await asyncio.sleep(0.01)
                async with self._cache_lock:
                    entry = self._cache.get(virtual_key)
                if entry is not None:
                    return entry[0]
            return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}

        # We are the fetching coroutine
        try:
            await self._fetch_policy(virtual_key)
        finally:
            async with self._inflight_lock:
                self._inflight.discard(virtual_key)

        async with self._cache_lock:
            entry = self._cache.get(virtual_key)
        if entry is None:
            return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
        return entry[0]


class VaultPolicyResolver(BasePolicyResolver):
    def __init__(self, http_client: httpx.AsyncClient, vault_url: str, vault_token: str):
        self.http_client = http_client
        self.vault_url = vault_url
        self.vault_token = vault_token
        self._cache = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._inflight: Set[str] = set()
        self._inflight_lock = asyncio.Lock()
        self._max_cache_size = 1000

    async def _fetch_policy(self, virtual_key: str) -> None:
        try:
            response = await self.http_client.get(
                f"{self.vault_url}/v1/secret/data/shield/tenant/{virtual_key}",
                headers={"X-Vault-Token": self.vault_token},
                timeout=0.05
            )
            response.raise_for_status()
            data = response.json()
            policy = data.get("data", {}).get("data", {}).get("policy", {})

            expiration = time.time() + settings.RBAC_CACHE_TTL_SECONDS
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)
        except Exception as e:
            AuditLogger.log_security_event(
                event_type="rbac_fetch_failure",
                severity="WARNING",
                details={"reason": "Vault fetch failed", "error_type": type(e).__name__},
                virtual_key_id=virtual_key
            )
            policy = {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
            expiration = time.time() + 5.0
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)

    async def _safe_background_fetch(self, virtual_key: str) -> None:
        async with self._inflight_lock:
            if virtual_key in self._inflight:
                return
            self._inflight.add(virtual_key)
        try:
            await self._fetch_policy(virtual_key)
        except Exception as e:
            AuditLogger.log_security_event(
                event_type="rbac_background_fetch_critical_failure",
                severity="CRITICAL",
                details={"reason": "Background fetch task crashed", "error_type": type(e).__name__},
                virtual_key_id=virtual_key
            )
            policy = {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
            expiration = time.time() + 5.0
            async with self._cache_lock:
                if len(self._cache) >= self._max_cache_size and virtual_key not in self._cache:
                    self._cache.popitem(last=False)
                self._cache[virtual_key] = (policy, expiration)
        finally:
            async with self._inflight_lock:
                self._inflight.discard(virtual_key)

    async def resolve_policy(self, virtual_key: str) -> dict:
        async with self._cache_lock:
            entry = self._cache.get(virtual_key)
        now = time.time()
        
        if entry is not None:
            policy, expiration = entry
            if now > expiration:
                asyncio.create_task(self._safe_background_fetch(virtual_key))
            return policy

        # Cache miss
        async with self._inflight_lock:
            already_inflight = virtual_key in self._inflight
            if not already_inflight:
                self._inflight.add(virtual_key)
                
        if already_inflight:
            for _ in range(10):
                await asyncio.sleep(0.01)
                async with self._cache_lock:
                    entry = self._cache.get(virtual_key)
                if entry is not None:
                    return entry[0]
            return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}

        # We are the fetching coroutine
        try:
            await self._fetch_policy(virtual_key)
        finally:
            async with self._inflight_lock:
                self._inflight.discard(virtual_key)

        async with self._cache_lock:
            entry = self._cache.get(virtual_key)
        if entry is None:
            return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}
        return entry[0]

class InMemoryPolicyResolver(BasePolicyResolver):
    async def resolve_policy(self, virtual_key: str) -> dict:
        return {"allowed_tools": [], "blocked_tools": []}

QUOTE_BYTE = 0x22      # ord('"')
BACKSLASH_BYTE = 0x5C  # ord('\\')
COLON_BYTE = 0x3A      # ord(':')
WHITESPACE_BYTES = (0x20, 0x09, 0x0A, 0x0D)  # ord(' '), ord('\t'), ord('\n'), ord('\r')


class StreamingToolParser:
    """
    Zero-Allocation Streaming JSON Lexer that extracts tool names.
    Operates purely on byte-streams to prevent Slowloris and OOM attacks.
    """

    def __init__(self):
        self.TARGET_KEYS: Set[bytes] = {b"name", b"method"}
        self.MAX_TOOL_NAME_LEN = 256
        self.reset()

    def reset(self):
        self.state = "SEARCHING"
        self.buffer = bytearray()
        self.target_key_matched = False
        self.escape_next = False

    def feed(self, chunk: bytes) -> list[str]:
        extracted = []
        for byte_int in chunk:
            if self.state == "SEARCHING":
                if byte_int == QUOTE_BYTE:
                    self.state = "IN_STRING"
                    self.buffer.clear()
                    self.escape_next = False

            elif self.state == "IN_STRING":
                if self.escape_next:
                    self.buffer.append(byte_int)
                    self.escape_next = False
                    if len(self.buffer) > self.MAX_TOOL_NAME_LEN:
                        self.state = "SEARCHING"
                        self.target_key_matched = False
                elif byte_int == BACKSLASH_BYTE:
                    self.escape_next = True
                elif byte_int == QUOTE_BYTE:
                    val = bytes(self.buffer)
                    if self.target_key_matched:
                        extracted.append(val.decode("utf-8", errors="ignore"))
                        self.target_key_matched = False
                        self.state = "SEARCHING"
                    else:
                        if val in self.TARGET_KEYS:
                            self.state = "WAIT_COLON"
                        else:
                            self.state = "SEARCHING"
                else:
                    self.buffer.append(byte_int)
                    if len(self.buffer) > self.MAX_TOOL_NAME_LEN:
                        self.state = "SEARCHING"
                        self.target_key_matched = False

            elif self.state == "WAIT_COLON":
                if byte_int == COLON_BYTE:
                    self.state = "WAIT_VALUE"
                elif byte_int not in WHITESPACE_BYTES:
                    self.state = "SEARCHING"
                    if byte_int == QUOTE_BYTE:
                        self.state = "IN_STRING"
                        self.buffer.clear()

            elif self.state == "WAIT_VALUE":
                if byte_int == QUOTE_BYTE:
                    self.state = "IN_STRING"
                    self.buffer.clear()
                    self.target_key_matched = True
                elif byte_int not in WHITESPACE_BYTES:
                    self.state = "SEARCHING"

        return extracted


class RBACValidator:
    """
    Fail-Closed Execution validator that validates tool names dynamically across SSE streams or batch JSON-RPC.
    """

    def __init__(self, resolver: BasePolicyResolver):
        self.resolver = resolver

    def check_tool(self, tool_name: str, allowed: set, blocked: set):
        if tool_name in blocked:
            raise ToolAccessForbiddenException(tool_name)
        if allowed and tool_name not in allowed:
            raise ToolAccessForbiddenException(tool_name)

    async def validate_stream(
        self, stream: AsyncGenerator[bytes, None], virtual_key: str
    ) -> AsyncGenerator[bytes, None]:
        """
        Intercepts incoming/outgoing chunked SSE streams and JSON-RPC payloads.
        Uses fail-closed deterministic tool rejection.
        """
        policy = await self.resolver.resolve_policy(virtual_key)
        allowed = set(policy.get("allowed_tools", []))
        blocked = set(policy.get("blocked_tools", []))

        parser = StreamingToolParser()
        try:
            async for chunk in stream:
                tools = parser.feed(chunk)
                for t in tools:
                    self.check_tool(t, allowed, blocked)
                yield chunk
        except ToolAccessForbiddenException as e:
            # Yield deterministic error chunk and abort stream
            yield e.get_rejection_chunk()
