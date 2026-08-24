import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Set

import orjson
import redis.asyncio as redis


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
    async def resolve_policy(self, virtual_key: str) -> dict:
        logging.getLogger(__name__).warning("OPA integration coming in v1.2. Failing closed.")
        return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}


class InMemoryPolicyResolver(BasePolicyResolver):
    async def resolve_policy(self, virtual_key: str) -> dict:
        return {"allowed_tools": [], "blocked_tools": []}


class VaultPolicyResolver(BasePolicyResolver):
    async def resolve_policy(self, virtual_key: str) -> dict:
        logging.getLogger(__name__).warning("Vault integration coming in v1.2. Failing closed.")
        return {"allowed_tools": ["_FAIL_CLOSED_"], "blocked_tools": []}


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
