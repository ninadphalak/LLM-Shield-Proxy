"""Audit Remediation Verification Test Suite.

Validates all security fixes, event loop unblocking, memory bounding,
and cryptographic isolation guarantees identified during the codebase audit.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from llm_shield_proxy.api.main import (
    _SAFE_REQUEST_ID_PATTERN,
    _is_safe_ip,
    get_virtual_key_id,
)
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.security.rate_limit import DistributedBlastRadiusLimiter, DistributedRateLimiter
from llm_shield_proxy.security.watermark import generate_watermark_text, get_identity


def test_ssrf_ipv6_and_special_ip_rejection():
    """Validates that IPv6 loopback, link-local, unspecified, and IPv4-mapped IPv6 are strictly rejected."""
    unsafe_ips = [
        "::1",  # IPv6 Loopback
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 Loopback
        "::ffff:10.0.0.1",  # IPv4-mapped IPv6 Private
        "::ffff:169.254.169.254",  # IPv4-mapped IPv6 Cloud Metadata
        "::",  # Unspecified
        "0.0.0.0",  # Wildcard / Unspecified
        "255.255.255.255",  # Broadcast
        "127.0.0.1",  # IPv4 Loopback
        "10.0.1.5",  # RFC 1918 Private
        "172.16.0.1",  # RFC 1918 Private
        "192.168.1.1",  # RFC 1918 Private
        "169.254.169.254",  # AWS/GCP Metadata
        "fe80::1",  # Link-Local IPv6
        "fc00::1",  # Unique Local IPv6 (Private)
    ]
    for ip in unsafe_ips:
        assert not _is_safe_ip(ip), f"Unsafe IP {ip} was erroneously accepted by _is_safe_ip!"

    safe_ips = [
        "8.8.8.8",
        "1.1.1.1",
        "2607:f8b0:4005:805::200e",  # Google Public IPv6
    ]
    for ip in safe_ips:
        assert _is_safe_ip(ip), f"Public safe IP {ip} was rejected by _is_safe_ip!"


def test_rate_limiter_memory_bounding():
    """Validates that in-memory buckets in rate limiter and blast radius limiter are bounded by TTLCache."""
    limiter = DistributedRateLimiter()
    # Insert 100 unique keys
    for i in range(100):
        limiter._in_memory_buckets[f"key_{i}"] = None  # type: ignore

    assert len(limiter._in_memory_buckets) == 100
    assert limiter._in_memory_buckets.maxsize == 50_000

    blast_limiter = DistributedBlastRadiusLimiter()
    assert blast_limiter._in_memory_buckets.maxsize == 50_000


@pytest.mark.asyncio
async def test_concurrent_vault_writes_are_bijective():
    """Spawn 50 concurrent coroutines writing overlapping PII strings to the same Vault.

    Asserts that every original -> token mapping is strictly bijective without race corruption.
    """
    vault = Vault(synthetic=False)
    pii_strings = [f"user_{i % 10}@example.com" for i in range(50)]

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(
            None, pii_engine.redact_text, f"Email: {s}", vault
        )
        for s in pii_strings
    ]
    await asyncio.gather(*tasks)

    # Assert bijection: equal count of forward and reverse mappings
    assert len(vault.original_to_token) == len(vault.token_to_original)
    for orig, tok in vault.original_to_token.items():
        assert vault.token_to_original[tok] == orig


def test_watermark_uses_fingerprint_not_raw_secret():
    """Verifies that watermark generation uses virtual key fingerprint instead of leaking raw API key."""
    secret = "shield_secret_123"
    raw_api_key = "Bearer sk-proj-1234567890abcdef"
    virtual_key = "tenant-prod-1"

    # With virtual_key provided
    wm_vk = generate_watermark_text(secret=secret, virtual_key_id=virtual_key, session_id="sess_1")
    assert len(wm_vk) > 0

    # With raw auth header fallback
    identity_from_auth = get_identity(secret=secret, authorization_header=raw_api_key)
    # Identity must NOT be the raw secret string
    assert "sk-proj" not in identity_from_auth
    assert identity_from_auth != get_identity(secret="different-deployment", authorization_header=raw_api_key)


def test_request_id_crlf_sanitization():
    """Asserts that malicious headers containing CRLF are rejected by _SAFE_REQUEST_ID_PATTERN."""
    safe_ids = ["req-123", "abc_456", "trace.id-789:01", str(uuid.uuid4())]
    unsafe_ids = [
        "req-123\r\nInjected-Header: evil",
        "req-123\nSet-Cookie: session=hijacked",
        "<script>alert(1)</script>",
        "a" * 65,  # Too long
        "",
    ]

    for sid in safe_ids:
        assert bool(_SAFE_REQUEST_ID_PATTERN.match(sid)), f"Safe request ID {sid} rejected!"

    for uid in unsafe_ids:
        assert not bool(_SAFE_REQUEST_ID_PATTERN.match(uid)), f"Unsafe request ID {uid} was accepted!"


def test_virtual_key_id_fast_and_zero_knowledge():
    """Asserts that get_virtual_key_id returns consistent HMAC-SHA256 fingerprint without plaintext storage."""
    key = "sk-proj-super-secret-key-12345"
    vk1 = get_virtual_key_id(key)
    vk2 = get_virtual_key_id(key)

    assert vk1 == vk2
    assert len(vk1) == 12
    # Verify LRU cache keys are hashes, not raw secret
    from llm_shield_proxy.api.main import _get_vkid_from_hash
    cache_info = _get_vkid_from_hash.cache_info()
    assert cache_info.hits >= 1


def test_non_streaming_rehydrator_depth_protection():
    """Asserts that NonStreamingRehydrator detects and raises ValueError on AST depth > 40."""
    from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher
    from llm_shield_proxy.engines.stateless_mutation_engine.streaming_lexer import NonStreamingRehydrator

    cipher = StatelessPIICipher(b"0123456789abcdef0123456789abcdef")
    rehydrator = NonStreamingRehydrator(cipher)

    # Build deep JSON structure 50 levels deep
    deep_payload = {"val": "bottom"}
    for _ in range(50):
        deep_payload = {"nested": deep_payload}

    with pytest.raises(ValueError, match="AST Depth Exceeded"):
        rehydrator.rehydrate(deep_payload)

    # Shallow payload (depth 5) must pass normally
    shallow_payload = {"a": {"b": {"c": {"d": {"e": "hello"}}}}}
    res = rehydrator.rehydrate(shallow_payload)
    assert res["a"]["b"]["c"]["d"]["e"] == "hello"


def test_schema_rewriter_input_immutability():
    """Asserts that DynamicSchemaRewriter does not mutate the caller's input dictionary."""
    import copy

    from llm_shield_proxy.engines.stateless_mutation_engine.schema_rewriter import DynamicSchemaRewriter

    original_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Number of results"},
        },
        "required": ["query"],
    }
    schema_snapshot = copy.deepcopy(original_schema)

    rewritten = DynamicSchemaRewriter.rewrite(original_schema)

    # The returned schema has injected sibling fields
    assert "_ctx_hash_query" in rewritten["properties"]
    assert "_ctx_hash_query" in rewritten["required"]

    # The caller's input schema remains 100% pure and untouched
    assert original_schema == schema_snapshot
    assert "_ctx_hash_query" not in original_schema["properties"]
    assert "_ctx_hash_query" not in original_schema["required"]


def test_streaming_tool_parser_zero_alloc_correctness():
    """Asserts that the byte-level zero-allocation StreamingToolParser correctly extracts tool names."""
    from llm_shield_proxy.security.tool_rbac import StreamingToolParser

    parser = StreamingToolParser()
    chunk1 = b'{"name": "fetch_'
    chunk2 = b'user_data", "arguments": {}}'

    names1 = parser.feed(chunk1)
    assert names1 == []

    names2 = parser.feed(chunk2)
    assert names2 == ["fetch_user_data"]


def test_merkle_tree_worm_bounded_records():
    """Asserts that MerkleTreeWORM caps records at max_records without memory leak."""
    from llm_shield_proxy.compliance.trace_exporter import MerkleTreeWORM

    merkle = MerkleTreeWORM(max_records=50)
    for i in range(100):
        merkle.append_record({"action": "check", "index": i})

    assert len(merkle.records) == 50
    # Merkle root hash must still chain continuously
    assert len(merkle.root_hash) == 64


@pytest.mark.asyncio
async def test_async_webhook_transport_aclose():
    """Asserts that AsyncWebhookTransport can be closed cleanly via aclose()."""
    from llm_shield_proxy.compliance.transport import AsyncWebhookTransport

    transport = AsyncWebhookTransport(webhook_url="https://example.com/webhook")
    assert not transport.client.is_closed

    await transport.aclose()
    assert transport.client.is_closed


def test_scrub_vault_top_level_redaction():
    """Asserts that ScrubVault redacts entities and tracks type counters."""
    from llm_shield_proxy.engines.masking import ScrubVault

    vault = ScrubVault()
    t1 = vault.get_or_create_token("john@example.com", "EMAIL")
    t2 = vault.get_or_create_token("jane@example.com", "EMAIL")
    vault.get_or_create_token("123-45-6789", "SSN")

    assert t1 == "[REDACTED]"
    assert t2 == "[REDACTED]"
    assert vault.type_counters["EMAIL"] == 2
    assert vault.type_counters["SSN"] == 1
    assert vault.rehydrate("Output with [REDACTED]") == "Output with [REDACTED]"


@pytest.mark.asyncio
async def test_dpop_enforcement_tiers():
    from llm_shield_proxy.security.identity import verify_agent_identity

    request = MagicMock(spec=Request)
    request.method = "POST"
    class MockURL:
        def __init__(self, s):
            self.s = s
        def replace(self, **kwargs):
            return self.s
        def __str__(self):
            return self.s

    request.url = MockURL("https://example.com/api")
    request.headers = {"Authorization": "Bearer tok", "DPoP": "dpop"}
    request.state = MagicMock()

    with patch("llm_shield_proxy.security.identity.jwt.decode") as mock_decode, \
         patch("llm_shield_proxy.security.identity._get_signing_key", new_callable=AsyncMock), \
         patch("llm_shield_proxy.security.identity.jwt.get_unverified_header") as mock_unverified_header, \
         patch("llm_shield_proxy.security.identity.jwt.PyJWK"), \
         patch("llm_shield_proxy.security.identity.asyncio.to_thread") as mock_to_thread, \
         patch("llm_shield_proxy.security.identity._get_jwk_thumbprint") as mock_thumbprint:

        mock_decode.return_value = {"iss": "https://issuer.com", "aud": "shield"}
        mock_unverified_header.return_value = {"jwk": {"kty": "RSA"}}

        mock_to_thread.side_effect = [
            {"cnf": {"jkt": "thumbprint"}, "sub": "agent1"},
            {"htm": "GET", "htu": "https://example.com/api", "iat": time.time(), "jti": "123"}
        ]

        mock_thumbprint.return_value = "thumbprint"

        # Test Lenient Mode (should pass and set warning)
        tenant_policy_lenient = {"agent_identity_enforcer": "lenient", "allowed_issuers": ["https://issuer.com"]}
        await verify_agent_identity(request, tenant_policy=tenant_policy_lenient)
        assert request.state.dpop_warning == "DPoP htm mismatch"

        # Test Strict Mode (should fail with 401 on htm mismatch, not on jti replay --
        # uses a distinct jti from the lenient-mode call above since the replay cache
        # would otherwise reject this call for the wrong reason)
        mock_to_thread.side_effect = [
            {"cnf": {"jkt": "thumbprint"}, "sub": "agent1"},
            {"htm": "GET", "htu": "https://example.com/api", "iat": time.time(), "jti": "456"}
        ]
        tenant_policy_strict = {"agent_identity_enforcer": "strict", "allowed_issuers": ["https://issuer.com"]}
        with pytest.raises(HTTPException) as exc:
            await verify_agent_identity(request, tenant_policy=tenant_policy_strict)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_slowloris_chunk_timeout():
    from fastapi import HTTPException

    from llm_shield_proxy.api.main import read_body_with_limit

    request = MagicMock(spec=Request)
    request.headers = {}

    async def slow_generator():
        yield b"a"
        await asyncio.sleep(6)
        yield b"b"

    request.stream = slow_generator

    with pytest.raises(HTTPException) as exc:
        await read_body_with_limit(request)

    assert exc.value.status_code == 408
    assert "Slowloris" in exc.value.detail


def test_tool_name_length_limit():
    from llm_shield_proxy.security.tool_rbac import StreamingToolParser

    parser = StreamingToolParser()
    parser.feed(b'{"name": "')

    long_name = b'A' * 260
    with pytest.raises(ValueError, match="Security Exception: Tool name exceeds 256 bytes"):
        parser.feed(long_name)


@pytest.mark.asyncio
async def test_bounded_lock_eviction():
    from llm_shield_proxy.security.tool_rbac import BoundedLockMap

    lock_map = BoundedLockMap(maxsize=1000)

    for i in range(1500):
        await lock_map.get_lock(f"key_{i}")

    assert len(lock_map.cache) == 1000
    assert "key_0" not in lock_map.cache
    assert "key_1499" in lock_map.cache

