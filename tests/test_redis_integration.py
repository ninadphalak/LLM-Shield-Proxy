"""End-to-end tests against a real Redis server.

Every test here talks to an actual Redis instance over the wire -- no mocks, no
fakes, no in-memory substitutes. CI provides one via a service container and
exports ``SHIELD_TEST_REDIS_URL``; locally the module skips when that variable is
absent.

These cover the two code paths that previously had no real-infrastructure
evidence at all:

* ``llm_shield_proxy.engines.vault.RedisVaultStore`` -- the distributed session
  vault, including cross-instance rehydration, rolling TTLs, tenant namespace
  isolation and the ``/readyz`` component probe that pings it.
* ``llm_shield_proxy.middleware.mcp_pruner.MCPDiscoveryPrunerMiddleware`` -- the
  MCP tool-catalog cache and its policy-version invalidation counter.

A deliberately note-worthy detail: the pruner decodes what it reads back from
Redis (``policy_version_bytes.decode("utf-8")``), so it requires a client in
byte mode. ``test_pruner_requires_a_byte_mode_redis_client`` pins that contract,
because a ``decode_responses=True`` client fails only at runtime.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncGenerator, Optional
from unittest.mock import patch

import orjson
import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient

from llm_shield_proxy.engines.vault import RedisVaultStore
from llm_shield_proxy.middleware.mcp_pruner import MCPDiscoveryPrunerMiddleware

REDIS_URL: Optional[str] = os.environ.get("SHIELD_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="SHIELD_TEST_REDIS_URL is not set; no real Redis server available",
)

# A port nothing listens on, used to prove the unhealthy branches are real
# failures rather than mocked return values.
DEAD_REDIS_URL = "redis://127.0.0.1:6390/0"


async def _await_key(client: "aioredis.Redis", key: str, expect=None, timeout: float = 2.0) -> bytes:
    """Wait for a background Redis write scheduled on the event loop to land."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = await client.get(key)
        if value is not None and (expect is None or expect(value)):
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(f"key {key!r} never reached the expected state in Redis")


@pytest.fixture
async def raw_redis() -> AsyncGenerator["aioredis.Redis", None]:
    """A separate byte-mode client used only to observe server-side state."""
    client = aioredis.from_url(REDIS_URL)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def store(raw_redis: "aioredis.Redis") -> RedisVaultStore:
    return RedisVaultStore(REDIS_URL)


# ---------------------------------------------------------------------------
# RedisVaultStore
# ---------------------------------------------------------------------------


async def test_async_vault_persists_mapping_to_real_redis(store, raw_redis):
    """A token minted through the async path lands in Redis as parseable JSON."""
    vault = await store.get_vault_async("sess-async", "tenant-a")
    token = vault.get_or_create_token("jane.doe@example.com", "EMAIL")

    stored = await _await_key(raw_redis, "tenant-a:sess-async")
    payload = orjson.loads(stored)

    assert payload["original_to_token"]["jane.doe@example.com"] == token
    assert payload["token_to_original"][token] == "jane.doe@example.com"
    assert payload["type_counters"]["EMAIL"] == 1
    assert payload["max_token_length"] == len(token)


async def test_async_vault_rehydrates_across_store_instances(store, raw_redis):
    """The horizontal-scaling claim: a second instance reads the first's mapping.

    This is the property the distributed vault exists for -- instance A masks,
    instance B rehydrates -- and it is only meaningful against a shared server.
    """
    vault_a = await store.get_vault_async("sess-shared", "tenant-a")
    token = vault_a.get_or_create_token("4111111111111111", "CREDIT_CARD")
    await _await_key(raw_redis, "tenant-a:sess-shared")

    other_instance = RedisVaultStore(REDIS_URL)
    vault_b = await other_instance.get_vault_async("sess-shared", "tenant-a")

    assert vault_b.token_to_original[token] == "4111111111111111"
    assert vault_b.rehydrate(f"card on file: {token}") == "card on file: 4111111111111111"
    # And the mapping stays bijective rather than minting a second token.
    assert vault_b.get_or_create_token("4111111111111111", "CREDIT_CARD") == token


def test_sync_vault_roundtrip_against_real_redis(store):
    """The synchronous compatibility path writes and reads the same key space."""
    vault = store.get_vault("sess-sync", "tenant-a")
    token = vault.get_or_create_token("Ada Lovelace", "PERSON")

    reloaded = store.get_vault("sess-sync", "tenant-a")
    assert reloaded.token_to_original[token] == "Ada Lovelace"
    assert reloaded.type_counters["PERSON"] == 1


async def test_vault_key_namespaces_are_isolated_by_tenant(store, raw_redis):
    """Two tenants using the same session id never observe each other's vault."""
    vault_a = await store.get_vault_async("collide", "tenant-a")
    token_a = vault_a.get_or_create_token("secret-a@example.com", "EMAIL")
    await _await_key(raw_redis, "tenant-a:collide")

    vault_b = await store.get_vault_async("collide", "tenant-b")

    assert vault_b.token_to_original == {}
    assert token_a not in vault_b.token_to_original
    assert await raw_redis.exists("tenant-b:collide") == 0


async def test_vault_applies_and_rolls_the_server_side_ttl(store, raw_redis):
    """Redis owns expiry: the key carries a real TTL and each touch renews it."""
    vault = await store.get_vault_async("sess-ttl", "tenant-a")
    vault.get_or_create_token("bob@example.com", "EMAIL")
    await _await_key(raw_redis, "tenant-a:sess-ttl")

    ttl = await raw_redis.ttl("tenant-a:sess-ttl")
    assert 0 < ttl <= store.ttl

    # Wind the TTL down, then prove a fresh retrieval rolls it forward.
    await raw_redis.expire("tenant-a:sess-ttl", 5)
    assert await raw_redis.ttl("tenant-a:sess-ttl") <= 5

    await store.get_vault_async("sess-ttl", "tenant-a")
    assert await raw_redis.ttl("tenant-a:sess-ttl") > 5


async def test_clear_session_deletes_the_key_on_the_server(store, raw_redis):
    vault = await store.get_vault_async("sess-clear", "tenant-a")
    vault.get_or_create_token("carol@example.com", "EMAIL")
    await _await_key(raw_redis, "tenant-a:sess-clear")

    await store.clear_session_async("sess-clear", "tenant-a")
    assert await raw_redis.exists("tenant-a:sess-clear") == 0

    vault2 = await store.get_vault_async("sess-clear2", "tenant-a")
    vault2.get_or_create_token("dave@example.com", "EMAIL")
    await _await_key(raw_redis, "tenant-a:sess-clear2")
    store.clear_session("sess-clear2", "tenant-a")
    assert await raw_redis.exists("tenant-a:sess-clear2") == 0


async def test_anonymous_requests_do_not_touch_redis(store, raw_redis):
    """No session id means an ephemeral vault and zero server-side state."""
    vault = await store.get_vault_async(None, "tenant-a")
    vault.get_or_create_token("eve@example.com", "EMAIL")
    await asyncio.sleep(0.05)

    assert await raw_redis.dbsize() == 0
    assert vault.save_callback is None


async def test_ping_distinguishes_a_live_server_from_a_dead_one(store):
    assert await store.ping_async() is True
    assert await RedisVaultStore(DEAD_REDIS_URL).ping_async() is False


async def test_corrupt_stored_payload_degrades_to_an_empty_vault(store, raw_redis):
    """A truncated or non-JSON value must not fault the request path."""
    await raw_redis.set("tenant-a:sess-corrupt", b"{not-json")

    vault = await store.get_vault_async("sess-corrupt", "tenant-a")
    assert vault.token_to_original == {}

    # And the vault is still usable, overwriting the bad value.
    token = vault.get_or_create_token("frank@example.com", "EMAIL")
    stored = await _await_key(
        raw_redis, "tenant-a:sess-corrupt", expect=lambda v: v != b"{not-json"
    )
    assert orjson.loads(stored)["token_to_original"][token] == "frank@example.com"


def test_readyz_probes_the_real_redis_backend(monkeypatch):
    """`/readyz`'s redis component is a live PING, not a mocked coroutine."""
    from llm_shield_proxy.api import health
    from llm_shield_proxy.api.main import app

    client = TestClient(app)

    monkeypatch.setattr(health, "vault_store", RedisVaultStore(REDIS_URL))
    health._readyz_cache.clear()
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["components"]["redis"] == "ok"

    monkeypatch.setattr(health, "vault_store", RedisVaultStore(DEAD_REDIS_URL))
    health._readyz_cache.clear()
    degraded = client.get("/readyz")
    assert degraded.status_code == 503
    assert degraded.json()["components"]["redis"] == "degraded"
    health._readyz_cache.clear()


# ---------------------------------------------------------------------------
# MCP discovery pruner
# ---------------------------------------------------------------------------


class _MockApp:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"app_response"})


class _PolicyResolver:
    async def resolve_policy(self, virtual_key: str) -> dict:
        return {"allowed_tools": ["safe_tool_1"], "blocked_tools": ["dangerous_exec"]}


class _CountingUpstream:
    """Stands in for the MCP server. Counts how often it is actually fetched."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def client_factory(self, *args, **kwargs):
        outer = self

        class _Resp:
            def raise_for_status(self):
                return None

            async def aiter_bytes(self):
                yield orjson.dumps(outer.payload)

        class _Stream:
            async def __aenter__(self):
                outer.calls += 1
                return _Resp()

            async def __aexit__(self, *exc):
                return None

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            def stream(self, *a, **kw):
                return _Stream()

        return _Client()


class _Receive:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.index = 0

    async def __call__(self):
        if self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            more = self.index < len(self.chunks) - 1
            self.index += 1
            return {"type": "http.request", "body": chunk, "more_body": more}
        await asyncio.sleep(3600)


class _Send:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message):
        self.messages.append(message)

    def body(self) -> bytes:
        return b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        )


def _scope(tenant: bytes = b"tenant-mcp") -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [
            (b"x-tenant-id", tenant),
            (b"x-virtual-key", b"key-1"),
            # Public IP literal: no DNS lookup, and it clears the egress guard.
            (b"x-upstream-url", b"http://93.184.216.34/tools"),
        ],
    }


UPSTREAM_TOOLS = {
    "jsonrpc": "2.0",
    "result": {
        "_meta": {"ttlMs": 120000},
        "tools": [
            {"name": "safe_tool_1", "desc": "allowed"},
            {"name": "dangerous_exec", "desc": "blocked"},
            {"name": "unlisted_tool", "desc": "not in allowlist"},
        ],
    },
}

TOOLS_LIST_REQUEST = orjson.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 7})


async def _run_pruner(pruner, scope, body=TOOLS_LIST_REQUEST) -> _Send:
    send = _Send()
    await pruner(scope, _Receive([body]), send)
    await asyncio.sleep(0.05)  # let the background cache write settle
    return send


async def test_pruner_writes_the_pruned_catalog_into_real_redis(raw_redis):
    """First call fetches upstream, prunes by RBAC, and caches with a real TTL."""
    upstream = _CountingUpstream(UPSTREAM_TOOLS)
    pruner = MCPDiscoveryPrunerMiddleware(_MockApp(), raw_redis, _PolicyResolver())

    with patch("httpx.AsyncClient", upstream.client_factory):
        send = await _run_pruner(pruner, _scope())

    body = orjson.loads(send.body())
    assert [t["name"] for t in body["result"]["tools"]] == ["safe_tool_1"]
    assert body["id"] == 7
    assert upstream.calls == 1

    keys = await raw_redis.keys("mcp:tools:tenant-mcp:*")
    assert len(keys) == 1, "pruned catalog was not cached in Redis"
    cached = orjson.loads(await raw_redis.get(keys[0]))
    assert [t["name"] for t in cached["result"]["tools"]] == ["safe_tool_1"]
    # ttlMs 120000 -> 120s, inside the 30..3600 clamp.
    assert 0 < await raw_redis.ttl(keys[0]) <= 120


async def test_pruner_second_call_is_served_from_the_redis_cache(raw_redis):
    upstream = _CountingUpstream(UPSTREAM_TOOLS)
    pruner = MCPDiscoveryPrunerMiddleware(_MockApp(), raw_redis, _PolicyResolver())

    with patch("httpx.AsyncClient", upstream.client_factory):
        await _run_pruner(pruner, _scope())
        second = await _run_pruner(pruner, _scope())

    assert upstream.calls == 1, "cache hit did not prevent a second upstream fetch"
    assert [t["name"] for t in orjson.loads(second.body())["result"]["tools"]] == ["safe_tool_1"]


async def test_pruner_cache_is_isolated_per_tenant(raw_redis):
    upstream = _CountingUpstream(UPSTREAM_TOOLS)
    pruner = MCPDiscoveryPrunerMiddleware(_MockApp(), raw_redis, _PolicyResolver())

    with patch("httpx.AsyncClient", upstream.client_factory):
        await _run_pruner(pruner, _scope(b"tenant-one"))
        await _run_pruner(pruner, _scope(b"tenant-two"))

    assert upstream.calls == 2, "one tenant's cache entry served another tenant"
    assert len(await raw_redis.keys("mcp:tools:tenant-one:*")) == 1
    assert len(await raw_redis.keys("mcp:tools:tenant-two:*")) == 1


NOTIFICATION = orjson.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})


async def test_list_changed_notification_increments_the_real_policy_version(raw_redis):
    """The invalidation counter is a real Redis INCR that does bust the cache.

    Note the version values asserted below: the counter starts *absent* and the
    read path substitutes ``"0"``, which is outside the range INCR can return,
    so the first INCR lands on ``1`` and genuinely changes the cache key.
    ``test_first_list_changed_notification_invalidates`` pins that first bump
    specifically -- it used to be a no-op, because the read default was ``"1"``.
    """
    upstream = _CountingUpstream(UPSTREAM_TOOLS)
    app = _MockApp()
    pruner = MCPDiscoveryPrunerMiddleware(app, raw_redis, _PolicyResolver())

    with patch("httpx.AsyncClient", upstream.client_factory):
        await _run_pruner(pruner, _scope())
        assert upstream.calls == 1

        await _run_pruner(pruner, _scope(), body=NOTIFICATION)
        assert await raw_redis.get("mcp:policy_version:tenant-mcp") == b"1"
        assert app.called, "the notification must still fall through to the app"

        await _run_pruner(pruner, _scope(), body=NOTIFICATION)
        assert await raw_redis.get("mcp:policy_version:tenant-mcp") == b"2"

        # The version is part of the cache key, so discovery must now refetch.
        await _run_pruner(pruner, _scope())

    assert upstream.calls == 2, "policy-version bump did not invalidate the cached catalog"
    assert len(await raw_redis.keys("mcp:tools:tenant-mcp:*")) == 2


async def test_first_list_changed_notification_invalidates(raw_redis):
    """A tenant's FIRST tools/list_changed must bust the cache.

    This used to fail: the read path defaulted a missing mcp:policy_version key
    to "1" while INCR on that same missing key also yields 1, so the first
    notification a tenant ever sent left the cache key unchanged and a stale
    tool catalog kept being served for the rest of the TTL. Every subsequent
    notification invalidated correctly, which is what made it easy to miss.
    """
    upstream = _CountingUpstream(UPSTREAM_TOOLS)
    pruner = MCPDiscoveryPrunerMiddleware(_MockApp(), raw_redis, _PolicyResolver())

    with patch("httpx.AsyncClient", upstream.client_factory):
        await _run_pruner(pruner, _scope())
        await _run_pruner(pruner, _scope(), body=NOTIFICATION)
        await _run_pruner(pruner, _scope())

    assert upstream.calls == 2, "first invalidation did not refresh the tool catalog"


async def test_pruner_requires_a_byte_mode_redis_client(raw_redis):
    """Pins the client contract the implementation actually depends on.

    ``_handle_discovery`` calls ``.decode("utf-8")`` on the value read back for
    ``mcp:policy_version:*``. With ``decode_responses=True`` that value is
    already a ``str`` and the call raises, which the broad ``except Exception``
    converts into a 502 -- so the misconfiguration is invisible except as
    upstream failures under load.
    """
    await raw_redis.set("mcp:policy_version:tenant-mcp", b"1")

    text_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        upstream = _CountingUpstream(UPSTREAM_TOOLS)
        pruner = MCPDiscoveryPrunerMiddleware(_MockApp(), text_client, _PolicyResolver())
        with patch("httpx.AsyncClient", upstream.client_factory):
            send = await _run_pruner(pruner, _scope())

        assert send.messages[0]["status"] == 502
        assert upstream.calls == 0
    finally:
        await text_client.aclose()
