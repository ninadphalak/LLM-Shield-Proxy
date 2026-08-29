"""Tests for the MCP JSON-RPC 2.0 gateway router: RBAC gating, sanitization, and discovery pruning."""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.api.mcp_router import get_mcp_policy_resolver
from llm_shield_proxy.security.tool_rbac import BasePolicyResolver

client = TestClient(app)

UPSTREAM_URL = "http://mcp-upstream.test/mcp"


class MockPolicyResolver(BasePolicyResolver):
    def __init__(self, policy: dict):
        self.policy = policy

    async def resolve_policy(self, virtual_key: str) -> dict:
        return self.policy


def _override_policy(policy: dict) -> None:
    app.dependency_overrides[get_mcp_policy_resolver] = lambda: MockPolicyResolver(policy)


def teardown_function(_fn) -> None:
    app.dependency_overrides.pop(get_mcp_policy_resolver, None)


def test_tools_call_authorized_execution_scrubs_pii(httpx_mock):
    """An allowed tool call is forwarded upstream with arguments scrubbed, and the response is scrubbed too."""
    _override_policy({"allowed_tools": ["search_docs"], "blocked_tools": []})

    def response_callback(request):
        import httpx

        sent = json.loads(request.content.decode("utf-8"))
        # The email in the forwarded arguments must already be scrubbed before it left the proxy.
        assert "leak@corp.com" not in json.dumps(sent)
        return httpx.Response(
            status_code=200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "Contact admin@upstream-secret.com for access"}]},
            },
        )

    httpx_mock.add_callback(response_callback, url=UPSTREAM_URL)

    response = client.post(
        "/v1/mcp",
        headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_docs", "arguments": {"query": "email me at leak@corp.com"}},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert "error" not in body
    result_text = body["result"]["content"][0]["text"]
    assert "admin@upstream-secret.com" not in result_text


def test_tools_call_blocked_returns_dash_32003_and_does_not_route_upstream(httpx_mock):
    """A tool not in the allowlist is rejected fail-closed with -32003 and never reaches upstream."""
    _override_policy({"allowed_tools": ["search_docs"], "blocked_tools": ["dangerous_exec"]})

    with patch("llm_shield_proxy.observability.audit.AuditLogger.log_security_event") as mock_log:
        response = client.post(
            "/v1/mcp",
            headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "dangerous_exec", "arguments": {}},
            },
        )

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "mcp_tool_forbidden"
        assert call_kwargs["severity"] == "CRITICAL"

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 2
    assert body["error"]["code"] == -32003
    assert body["error"]["message"] == "Tool forbidden for active role"

    # No upstream request registered in httpx_mock: if the router had routed upstream, this would raise.
    assert len(httpx_mock.get_requests()) == 0


def test_tools_list_dynamic_pruning(httpx_mock):
    """tools/list intercepts the upstream catalog and prunes tools not permitted for the active Virtual Key."""
    _override_policy({"allowed_tools": ["tool_a"], "blocked_tools": ["tool_b"]})

    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM_URL,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "tools": [
                    {"name": "tool_a", "description": "Allowed"},
                    {"name": "tool_b", "description": "Blocked"},
                    {"name": "tool_c", "description": "Not explicitly allowed"},
                ]
            },
        },
    )

    response = client.post(
        "/v1/mcp",
        headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    )

    assert response.status_code == 200
    body = response.json()
    tools = body["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "tool_a"


def test_tools_call_arguments_use_format_preserving_synthetic_masking(httpx_mock):
    """Inbound argument scrubbing must not replace structured fields with a literal '[REDACTED]'.

    A tool with strict schema validation (Pydantic EmailStr, etc.) would reject a bare
    '[REDACTED]' string. The proxy must substitute a format-preserving synthetic value instead.
    """
    _override_policy({"allowed_tools": ["send_email"], "blocked_tools": []})

    captured = {}

    def response_callback(request):
        import httpx

        captured["sent"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(status_code=200, json={"jsonrpc": "2.0", "id": 4, "result": {"ok": True}})

    httpx_mock.add_callback(response_callback, url=UPSTREAM_URL)

    response = client.post(
        "/v1/mcp",
        headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": {"to": "leak@corp.com"}},
        },
    )

    assert response.status_code == 200
    sent_to = captured["sent"]["params"]["arguments"]["to"]
    assert sent_to != "leak@corp.com"
    assert sent_to != "[REDACTED]"
    assert "[" not in sent_to and "]" not in sent_to
    # Faker's synthetic replacement must still look like a real email address.
    assert "@" in sent_to and "." in sent_to.split("@", 1)[1]


def test_tools_list_pruning_preserves_pagination_cursor(httpx_mock):
    """Pruning all tools from a page must not corrupt the nextCursor pagination token."""
    _override_policy({"allowed_tools": ["tool_only_on_page_2"], "blocked_tools": []})

    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM_URL,
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "result": {
                "tools": [{"name": "tool_a", "description": "Not allowed"}],
                "nextCursor": "page-2-token",
            },
        },
    )

    response = client.post(
        "/v1/mcp",
        headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["tools"] == []
    assert result["nextCursor"] == "page-2-token"


def test_batch_json_rpc_requests_processed_independently(httpx_mock):
    """A JSON-RPC 2.0 batch array must be processed per-item, not rejected wholesale."""
    _override_policy({"allowed_tools": ["safe_tool"], "blocked_tools": ["dangerous_tool"]})

    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM_URL,
        json={"jsonrpc": "2.0", "id": 10, "result": {"content": [{"type": "text", "text": "ok"}]}},
    )

    with patch("llm_shield_proxy.observability.audit.AuditLogger.log_security_event") as mock_log:
        response = client.post(
            "/v1/mcp",
            headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
            json=[
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {"name": "safe_tool", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "dangerous_tool", "arguments": {}},
                },
            ],
        )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2

    by_id = {item["id"]: item for item in body}
    assert "error" not in by_id[10]
    assert by_id[10]["result"]["content"][0]["text"] == "ok"
    assert by_id[11]["error"]["code"] == -32003

    # Only the authorized batch item should have reached the upstream MCP server.
    assert len(httpx_mock.get_requests()) == 1
    mock_log.assert_called_once()


def test_batch_notifications_without_id_yield_no_response_entry(httpx_mock):
    """Batch items without an 'id' are JSON-RPC notifications and must not appear in the response array."""
    _override_policy({"allowed_tools": ["safe_tool"], "blocked_tools": []})

    # Both batch items are authorized and forwarded upstream (the notification included),
    # so two upstream responses must be registered.
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM_URL,
        json={"jsonrpc": "2.0", "id": 20, "result": {"ok": True}},
    )
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM_URL,
        json={"jsonrpc": "2.0", "id": None, "result": {"ok": True}},
    )

    response = client.post(
        "/v1/mcp",
        headers={"X-Shield-Virtual-Key": "test-key", "X-Shield-Upstream-URL": UPSTREAM_URL},
        json=[
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "safe_tool", "arguments": {}}},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "safe_tool", "arguments": {}}},
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == 20
