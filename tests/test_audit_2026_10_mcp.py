"""Regression tests for the 2026-10 security audit findings on the MCP gateway.

Covers: unauthenticated /v1/mcp identity, resources/read authorization, the upstream
JSON-RPC error member scrub, the egress-scan depth gap, the Vault path injection, raw
virtual keys in the audit chain, the uncapped /v1/mcp body and WHATWG URL forms.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.api.mcp_router import get_mcp_policy_resolver
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.security.egress_guard import (
    EgressPolicyViolationError,
    evaluate_url,
    find_urls,
    scan_arguments,
)
from llm_shield_proxy.security.key_fingerprint import get_virtual_key_id
from llm_shield_proxy.security.tool_rbac import BasePolicyResolver, VaultPolicyResolver

client = TestClient(app)

UPSTREAM_URL = "http://93.184.216.34/mcp"
REAL_KEY = "vk-real-dummy"
AUDIT_LOG = "llm_shield_proxy.observability.audit.AuditLogger.log_security_event"


class RecordingResolver(BasePolicyResolver):
    def __init__(self, policy: dict):
        self.policy = policy
        self.seen: list = []

    async def resolve_policy(self, virtual_key: str) -> dict:
        self.seen.append(virtual_key)
        return self.policy


def _override(policy: dict) -> RecordingResolver:
    resolver = RecordingResolver(policy)
    app.dependency_overrides[get_mcp_policy_resolver] = lambda: resolver
    return resolver


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setattr(settings, "_valid_virtual_keys_set", frozenset({REAL_KEY}))
    monkeypatch.setattr(settings, "OVERRIDE_CLIENT_AUTH", False)
    monkeypatch.setattr(settings, "MCP_EMPTY_ALLOWLIST_MODE", "DENY_ALL")
    monkeypatch.delenv("UPSTREAM_MCP_BASE_URL", raising=False)
    yield
    app.dependency_overrides.pop(get_mcp_policy_resolver, None)


def _post(body, key=REAL_KEY, upstream=UPSTREAM_URL, **extra_headers):
    headers = dict(extra_headers)
    if key is not None:
        headers["X-Shield-Virtual-Key"] = key
    if upstream is not None:
        headers["X-Shield-Upstream-URL"] = upstream
    return client.post("/v1/mcp", headers=headers, json=body)


def _call(name="fetch", arguments=None, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}, **params}}


def _ok(httpx_mock, payload=None):
    httpx_mock.add_response(url=UPSTREAM_URL, json=payload or {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})


# --- mcp_router:_extract_virtual_key:unauthenticated-identity ---------------------------


def test_no_credential_is_refused_before_policy_or_upstream(httpx_mock):
    resolver = _override({"allowed_tools": ["fetch"], "allowed_resources": ["*"]})
    r = _post({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "file:///dummy.txt"}}, key=None)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == -32001
    assert resolver.seen == []
    assert httpx_mock.get_requests() == []


def test_key_not_in_valid_virtual_keys_is_refused(httpx_mock):
    """A revoked key keeps no tool authority on /v1/mcp even if its policy entry remains."""
    resolver = _override({"allowed_tools": ["fetch"]})
    r = _post(_call(), key="vk-revoked")
    assert r.status_code == 401
    assert resolver.seen == []
    assert httpx_mock.get_requests() == []


def test_empty_key_set_fails_closed(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "_valid_virtual_keys_set", frozenset())
    _override({"allowed_tools": ["fetch"]})
    assert _post(_call()).status_code == 401
    assert httpx_mock.get_requests() == []


def test_non_ascii_key_is_a_401_not_a_500(httpx_mock):
    _override({"allowed_tools": ["fetch"]})
    r = client.post("/v1/mcp", headers={"X-Shield-Virtual-Key": "vk-\xe9".encode("latin-1")}, json=_call())
    assert r.status_code == 401


def test_matched_key_is_used_for_policy_lookup(httpx_mock):
    resolver = _override({"allowed_tools": ["fetch"]})
    _ok(httpx_mock)
    r = _post(_call())
    assert r.status_code == 200 and "result" in r.json()
    assert resolver.seen == [REAL_KEY]


def test_bearer_token_authenticates(httpx_mock):
    resolver = _override({"allowed_tools": ["fetch"]})
    _ok(httpx_mock)
    r = _post(_call(), key=None, Authorization=f"Bearer {REAL_KEY}")
    assert "result" in r.json()
    assert resolver.seen == [REAL_KEY]


def test_override_client_auth_is_anonymous_and_ignores_upstream_header(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "OVERRIDE_CLIENT_AUTH", True)
    resolver = _override({"allowed_tools": ["fetch"]})
    r = _post(_call(), key="vk-asserted")
    assert resolver.seen == ["anonymous"]
    # The client-supplied upstream is not honoured for an unauthenticated caller.
    assert r.json()["error"]["message"] == "No upstream MCP server configured"
    assert httpx_mock.get_requests() == []

    monkeypatch.setenv("UPSTREAM_MCP_BASE_URL", UPSTREAM_URL)
    _ok(httpx_mock)
    assert "result" in _post(_call(), key="vk-asserted", upstream="http://93.184.216.99/evil").json()
    assert str(httpx_mock.get_requests()[0].url) == UPSTREAM_URL


# --- resources-read-no-authorization -----------------------------------------------------


def _read(uri):
    return {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}}


def test_resources_read_denied_for_deny_all_policy(httpx_mock):
    _override({"allowed_tools": [], "blocked_tools": []})
    r = _post(_read("file:///internal/runbook.md"))
    assert r.json()["error"]["code"] == -32003
    assert httpx_mock.get_requests() == []


def test_fail_closed_sentinel_denies_every_method(httpx_mock):
    _override({"allowed_tools": ["_FAIL_CLOSED_"], "allowed_resources": ["*"]})
    for body in (
        _read("file:///internal/runbook.md"),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        _call(name="_FAIL_CLOSED_"),
    ):
        assert _post(body).json()["error"]["code"] == -32003
    assert httpx_mock.get_requests() == []


def test_resources_read_requires_allowed_resources(httpx_mock):
    _override({"allowed_tools": ["fetch"]})
    assert _post(_read("file:///docs/a.md")).json()["error"]["code"] == -32003
    assert httpx_mock.get_requests() == []


def test_resources_read_allowed_by_pattern_only(httpx_mock):
    _override({"allowed_tools": ["fetch"], "allowed_resources": ["file:///docs/*"]})
    for uri in ("file:///internal/x", "file:///docs/../internal/x", "file:///docs/%2E%2E/x", None):
        assert _post(_read(uri)).json()["error"]["code"] == -32003
    assert httpx_mock.get_requests() == []

    _ok(httpx_mock, {"jsonrpc": "2.0", "id": 1, "result": {"contents": [{"uri": "file:///docs/a.md", "text": "hi"}]}})
    r = _post(_read("file:///docs/a.md"))
    assert r.json()["result"]["contents"][0]["text"] == "hi"


# --- upstream-error-member-unscrubbed ----------------------------------------------------


def test_upstream_error_member_is_scrubbed(httpx_mock):
    _override({"allowed_tools": ["lookup"]})
    _ok(
        httpx_mock,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "lookup failed for SSN 123-45-6789", "data": {"row": "a.b@example.com"}},
        },
    )
    body = _post(_call(name="lookup", arguments={"q": "x"})).json()
    dumped = json.dumps(body)
    assert "123-45-6789" not in dumped
    assert "a.b@example.com" not in dumped
    assert body["error"]["code"] == -32000


# --- find_urls-depth-guard-mismatch ------------------------------------------------------


def _nest(leaf, levels):
    for _ in range(levels):
        leaf = {"n": leaf}
    return leaf


def test_find_urls_fails_closed_past_max_depth():
    with pytest.raises(EgressPolicyViolationError) as exc:
        find_urls(_nest("http://localhost:2375/x", 21))
    assert exc.value.reason == "max_depth_exceeded"


@pytest.mark.parametrize(
    "body",
    [
        _call(arguments=_nest("http://localhost:2375/x", 20)),
        _call(_meta=_nest("http://127.0.0.1:2375/x", 20)),
    ],
)
def test_deep_url_in_params_is_never_forwarded(httpx_mock, body):
    _override({"allowed_tools": ["fetch"]})
    r = _post(body)
    assert "error" in r.json()
    assert httpx_mock.get_requests() == []


# --- tool_rbac:VaultPolicyResolver:_fetch_policy:unencoded-virtual-key-path --------------


def _vault(handler):
    seen = []

    def _h(request):
        seen.append(request.url.raw_path.decode())
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return VaultPolicyResolver(http, "http://vault.test:8200", "tok"), seen


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["../../../../sys/health", "vk-a?version=1", "vk-a#x", "a/b"])
async def test_vault_key_is_one_encoded_path_segment(key):
    resolver, seen = _vault(lambda r: httpx.Response(200, json={"initialized": True}))
    policy = await resolver.resolve_policy(key)
    assert policy["allowed_tools"] == ["_FAIL_CLOSED_"]
    assert len(seen) == 1
    tail = seen[0].split("/v1/secret/data/shield/tenant/", 1)[1]
    assert "/" not in tail and "?" not in tail and "#" not in tail


@pytest.mark.asyncio
async def test_vault_dot_segment_key_is_never_requested():
    resolver, seen = _vault(lambda r: httpx.Response(200, json={"data": {"data": {"policy": {"allowed_tools": ["x"]}}}}))
    policy = await resolver.resolve_policy("..")
    assert policy["allowed_tools"] == ["_FAIL_CLOSED_"]
    assert seen == []


@pytest.mark.asyncio
async def test_vault_response_without_policy_fails_closed_and_valid_policy_loads():
    resolver, _ = _vault(lambda r: httpx.Response(200, json={"data": {"data": {}}}))
    assert (await resolver.resolve_policy("vk-a"))["allowed_tools"] == ["_FAIL_CLOSED_"]

    good = {"allowed_tools": ["read"], "blocked_tools": []}
    resolver, seen = _vault(lambda r: httpx.Response(200, json={"data": {"data": {"policy": good}}}))
    assert await resolver.resolve_policy("vk-b") == good
    assert seen == ["/v1/secret/data/shield/tenant/vk-b"]


# --- raw-virtual-key-as-audit-identity ---------------------------------------------------


def test_audit_records_fingerprint_not_raw_key(httpx_mock):
    _override({"allowed_tools": ["fetch"]})
    with patch(AUDIT_LOG) as log:
        _post(_call(name="forbidden_tool"))
        _post(_call(arguments={"u": "http://169.254.169.254/latest/meta-data/"}))
    assert log.call_count == 2
    for call in log.call_args_list:
        kwargs = call.kwargs
        assert kwargs["virtual_key_id"] == get_virtual_key_id(REAL_KEY)
        assert REAL_KEY not in json.dumps(kwargs, default=str)


@pytest.mark.asyncio
async def test_rbac_resolver_failure_audits_fingerprint():
    resolver, _ = _vault(lambda r: httpx.Response(500))
    with patch(AUDIT_LOG) as log:
        await resolver.resolve_policy(REAL_KEY)
    assert log.call_args.kwargs["virtual_key_id"] == get_virtual_key_id(REAL_KEY)


# --- uncapped-request-body (MCP half) ----------------------------------------------------


def test_over_cap_body_is_413_and_unauthenticated_is_401_first(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "MAX_PAYLOAD_SIZE_BYTES", 4096)
    big = json.dumps([{}] * 4000).encode()
    r = client.post("/v1/mcp", headers={"X-Shield-Virtual-Key": REAL_KEY}, content=big)
    assert r.status_code == 413
    r = client.post("/v1/mcp", content=big)
    assert r.status_code == 401
    assert httpx_mock.get_requests() == []


# --- egress_guard:find_urls:whatwg-parser-differential -----------------------------------

WHATWG_DIVERGENT = [
    "http://metadata.google.internal\\@93.184.216.34/computeMetadata/v1/",
    "http://169.254.169.254\\@public.example/latest/meta-data/",
    "http:/metadata.google.internal/computeMetadata/v1/",
    "http:\\\\169.254.169.254/latest/meta-data/",
    "http:169.254.169.254/latest/meta-data/",
    "HTTP:/169.254.169.254/",
    "http:///169.254.169.254/",
    "http://93.184.216.34\n@169.254.169.254/",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", WHATWG_DIVERGENT)
async def test_whatwg_divergent_forms_fail_closed(url):
    async def resolver(host):
        return ["93.184.216.34"]

    with pytest.raises(EgressPolicyViolationError):
        await scan_arguments({"arguments": {"url": url}}, None, resolver=resolver)


@pytest.mark.parametrize("url", WHATWG_DIVERGENT[:4])
def test_whatwg_divergent_forms_are_not_forwarded(httpx_mock, url):
    _override({"allowed_tools": ["fetch"]})
    assert _post(_call(arguments={"url": url})).json()["error"]["code"] == -32003
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_canonical_public_url_still_passes():
    async def resolver(host):
        return ["93.184.216.34"]

    await evaluate_url("https://api.example.com/a/b", None, resolver=resolver)
    await scan_arguments({"q": "see https://api.example.com/x\nand more text"}, None, resolver=resolver)


def test_streaming_validator_never_grants_the_fail_closed_sentinel():
    from llm_shield_proxy.security.tool_rbac import RBACValidator, ToolAccessForbiddenException

    with pytest.raises(ToolAccessForbiddenException):
        RBACValidator(RecordingResolver({})).check_tool("_FAIL_CLOSED_", {"_FAIL_CLOSED_"}, set())
