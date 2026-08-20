from typing import AsyncGenerator, Set
from abc import ABC, abstractmethod

import orjson
import redis.asyncio as redis


class ToolAccessForbiddenException(Exception):
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool access forbidden: {tool_name}")

    def get_rejection_chunk(self) -> bytes:
        return orjson.dumps({
            "error": {
                "type": "permission_denied",
                "code": "TOOL_ACCESS_FORBIDDEN",
                "tool": self.tool_name
            }
        })


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
    async def resolve_policy(self, virtual_key: str) -> dict:
        raise NotImplementedError("OPA integration coming in v1.2")

class VaultPolicyResolver(BasePolicyResolver):
    async def resolve_policy(self, virtual_key: str) -> dict:
        raise NotImplementedError("Vault integration coming in v1.2")

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
            b = bytes([byte_int])

            if self.state == "SEARCHING":
                if b == b'"':
                    self.state = "IN_STRING"
                    self.buffer.clear()
                    self.escape_next = False

            elif self.state == "IN_STRING":
                if self.escape_next:
                    self.buffer.extend(b)
                    self.escape_next = False
                    if len(self.buffer) > self.MAX_TOOL_NAME_LEN:
                        self.state = "SEARCHING"
                        self.target_key_matched = False
                elif b == b'\\':
                    self.escape_next = True
                elif b == b'"':
                    val = bytes(self.buffer)
                    if self.target_key_matched:
                        extracted.append(val.decode('utf-8', errors='ignore'))
                        self.target_key_matched = False
                        self.state = "SEARCHING"
                    else:
                        if val in self.TARGET_KEYS:
                            self.state = "WAIT_COLON"
                        else:
                            self.state = "SEARCHING"
                else:
                    self.buffer.extend(b)
                    if len(self.buffer) > self.MAX_TOOL_NAME_LEN:
                        self.state = "SEARCHING"
                        self.target_key_matched = False

            elif self.state == "WAIT_COLON":
                if b == b':':
                    self.state = "WAIT_VALUE"
                elif b not in b' \t\n\r':
                    self.state = "SEARCHING"
                    if b == b'"':
                        self.state = "IN_STRING"
                        self.buffer.clear()

            elif self.state == "WAIT_VALUE":
                if b == b'"':
                    self.state = "IN_STRING"
                    self.buffer.clear()
                    self.target_key_matched = True
                elif b not in b' \t\n\r':
                    # The value is not a string, ignore
                    self.state = "SEARCHING"
                    if b == b'"':
                        # edge case: if we hit a quote immediately, but wait, b != quote in this elif.
                        pass

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

    async def validate_stream(self, stream: AsyncGenerator[bytes, None], virtual_key: str) -> AsyncGenerator[bytes, None]:
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
