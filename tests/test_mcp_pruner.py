import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from llm_shield_proxy.middleware.mcp_pruner import MCPDiscoveryPrunerMiddleware


class MockApp:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"app_response"})


class MockPolicyResolver:
    async def resolve_policy(self, virtual_key: str) -> dict:
        return {
            "allowed_tools": ["safe_tool_1", "safe_tool_2"],
            "blocked_tools": ["dangerous_exec", "rm_rf"]
        }


class MockResponse:
    def __init__(self, json_data):
        self.json_data = json_data

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        # Yield the json in chunks to simulate stream
        raw = orjson.dumps(self.json_data)
        yield raw[:10]
        yield raw[10:]


class MockStreamContext:
    def __init__(self, json_data):
        self.resp = MockResponse(json_data)

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockAsyncClient:
    def __init__(self, json_data):
        self.json_data = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def stream(self, method, url, **kwargs):
        return MockStreamContext(self.json_data)


def build_scope(headers: list = None) -> dict:
    if headers is None:
        headers = []
    return {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
    }


async def mock_receive(body_chunks: list[bytes]) -> AsyncGenerator[dict, None]:
    for i, chunk in enumerate(body_chunks):
        more = i < len(body_chunks) - 1
        yield {"type": "http.request", "body": chunk, "more_body": more}


class ReceiveIterator:
    def __init__(self, chunks):
        self.chunks = chunks
        self.index = 0

    async def __call__(self):
        if self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            more = self.index < len(self.chunks) - 1
            self.index += 1
            return {"type": "http.request", "body": chunk, "more_body": more}
        # Hang forever like a real ASGI server if we await again when no more body is expected
        await asyncio.sleep(3600)


class SendRecorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_notifications_list_changed():
    """Test that notifications/tools/list_changed drops cache gracefully and doesn't crash on missing id."""
    redis_mock = AsyncMock()
    app = MockApp()
    pruner = MCPDiscoveryPrunerMiddleware(app, redis_mock, MockPolicyResolver())

    req_body = orjson.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/tools/list_changed",
        # Notice intentionally omitted 'id'
    })

    scope = build_scope([
        (b"x-tenant-id", b"tenant-123")
    ])

    receiver = ReceiveIterator([req_body])
    sender = SendRecorder()

    await pruner(scope, receiver, sender)

    # Allow background tasks to fire
    await asyncio.sleep(0.01)

    # Expect cache invalidation via policy version bump
    redis_mock.incr.assert_called_once_with("mcp:policy_version:tenant-123")

    # App should have been called
    assert len(sender.messages) == 2
    assert sender.messages[1]["body"] == b"app_response"


@pytest.mark.asyncio
async def test_tools_list_rbac_pruning_and_ttl():
    """Test that tools/list intercepts, applies RBAC, clamps TTL, and preserves id."""
    redis_mock = AsyncMock()
    redis_mock.get.side_effect = lambda k: b"1" if "policy_version" in k else None

    app = MockApp()
    pruner = MCPDiscoveryPrunerMiddleware(app, redis_mock, MockPolicyResolver())

    req_body = orjson.dumps({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 999
    })

    scope = build_scope([
        (b"x-tenant-id", b"tenant-123"),
        (b"x-virtual-key", b"my-key"),
        (b"x-upstream-url", b"http://93.184.216.34/tools")
    ])

    receiver = ReceiveIterator([req_body])
    sender = SendRecorder()

    upstream_mock_response = {
        "jsonrpc": "2.0",
        "result": {
            "_meta": {"ttlMs": 9000000}, # 9000 seconds, which should be clamped to 3600
            "tools": [
                {"name": "safe_tool_1", "desc": "Good"},
                {"name": "dangerous_exec", "desc": "Bad"},
                {"name": "unknown_tool", "desc": "Not explicitly allowed if policy has allowed_tools"}
            ]
        }
    }

    with patch("httpx.AsyncClient", return_value=MockAsyncClient(upstream_mock_response)):
        await pruner(scope, receiver, sender)

    await asyncio.sleep(0.01) # let background task run

    # Verify Response Downstream
    assert len(sender.messages) >= 2
    start_msg = sender.messages[0]
    body_msgs = [m for m in sender.messages if m["type"] == "http.response.body"]
    full_body = b"".join(m.get("body", b"") for m in body_msgs)

    assert start_msg["status"] == 200
    resp_json = orjson.loads(full_body)

    assert resp_json["id"] == 999
    assert len(resp_json["result"]["tools"]) == 1
    assert resp_json["result"]["tools"][0]["name"] == "safe_tool_1"

    # Verify Redis Background Set
    set_calls = redis_mock.set.call_args_list
    assert len(set_calls) == 1
    args, kwargs = set_calls[0]

    # TTL should be clamped to 3600
    assert kwargs["ex"] == 3600
    assert "mcp:tools:tenant-123:" in args[0]

    # Payload cached must match what was sent downstream
    assert orjson.loads(args[1]) == resp_json


@pytest.mark.asyncio
async def test_upstream_failure_fail_closed():
    """Test that upstream failure yields an RFC 7807 502 error."""
    redis_mock = AsyncMock()
    redis_mock.get.side_effect = lambda k: b"1" if "policy_version" in k else None

    app = MockApp()
    pruner = MCPDiscoveryPrunerMiddleware(app, redis_mock, MockPolicyResolver())

    req_body = orjson.dumps({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 1
    })

    scope = build_scope([
        (b"x-tenant-id", b"tenant-123"),
        (b"x-upstream-url", b"http://93.184.216.34/tools")
    ])

    receiver = ReceiveIterator([req_body])
    sender = SendRecorder()

    class FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url, **kwargs):
            raise ConnectionError("Upstream is dead")

    with patch("llm_shield_proxy.observability.audit.AuditLogger.log_security_event") as mock_log:
        with patch("httpx.AsyncClient", return_value=FailingAsyncClient()):
            await pruner(scope, receiver, sender)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["severity"] == "CRITICAL"
        assert call_kwargs["event_type"] == "mcp_upstream_failure"

    assert len(sender.messages) == 2
    assert sender.messages[0]["status"] == 502

    error_resp = orjson.loads(sender.messages[1]["body"])
    assert error_resp["status"] == 502
    assert error_resp["title"] == "MCP Upstream Unavailable"
