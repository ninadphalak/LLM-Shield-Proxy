import time
import unittest.mock
from typing import AsyncGenerator

import orjson
import pytest

from llm_shield_proxy.compliance.trace_exporter import DecisionTraceExporter
from llm_shield_proxy.security.tool_rbac import (
    BasePolicyResolver,
    RBACValidator,
    RedisPolicyResolver,
    StreamingToolParser,
)


class MockPolicyResolver(BasePolicyResolver):
    def __init__(self, policy: dict):
        self.policy = policy

    async def resolve_policy(self, virtual_key: str) -> dict:
        return self.policy


@pytest.fixture
def mock_resolver():
    policy = {
        "allowed_tools": ["search_docs", "calculate_mortgage", "get_weather", "good_tool"],
        "blocked_tools": ["execute_sql", "shell_exec", "read_local_file", "bad_tool"],
    }
    return MockPolicyResolver(policy)


@pytest.mark.asyncio
async def test_slowloris_chunk_splitting(mock_resolver):
    parser = StreamingToolParser()
    payload = b'{"function": {"name": "get_weather", "arguments": "{}"}}'

    extracted = []
    # Feed 1 byte at a time
    for i in range(len(payload)):
        chunk = payload[i : i + 1]
        tools = parser.feed(chunk)
        extracted.extend(tools)

    assert extracted == ["get_weather"]


@pytest.mark.asyncio
async def test_batch_json_rpc_injection(mock_resolver):
    validator = RBACValidator(mock_resolver)

    payload = b'[{"method": "good_tool"}, {"method": "bad_tool"}]'

    async def mock_stream() -> AsyncGenerator[bytes, None]:
        yield payload

    # Read the output stream
    output_chunks = []
    async for chunk in validator.validate_stream(mock_stream(), "test_key"):
        output_chunks.append(chunk)

    # The stream should have been aborted and yielded an error chunk
    assert len(output_chunks) == 1
    error_json = orjson.loads(output_chunks[0])
    assert "error" in error_json
    assert error_json["error"]["code"] == "TOOL_ACCESS_FORBIDDEN"
    assert error_json["error"]["tool"] == "bad_tool"


@pytest.mark.asyncio
async def test_log_and_schema_injection():
    exporter = DecisionTraceExporter()

    # Malformed tool name with newline and null byte
    malicious_tool_name = 'get_weather\n\x00\r"'

    exporter.record_decision(
        tenant_id="tenant-123",
        virtual_key_hash="vk_hash",
        redacted_prompt_hash="prompt_hash",
        tool_name=malicious_tool_name,
        rbac_decision="DENY",
        payload_entropy=3.14,
    )

    oscal_artifact = exporter.generate_oscal_artifact()

    # Verify that the generated OSCAL artifact is still valid JSON
    # If injection succeeded, this would throw JSONDecodeError
    parsed = orjson.loads(oscal_artifact)
    assert parsed["assessment-results"]["uuid"] is not None

    # Ensure the hashes are computed
    assert len(exporter.merkle_tree.records) == 1
    assert exporter.merkle_tree.records[0]["payload"]["Tool_Name"] == malicious_tool_name


@pytest.mark.asyncio
async def test_latency_overhead(mock_resolver):
    validator = RBACValidator(mock_resolver)

    payload = b'{"delta": {"tool_calls": [{"function": {"name": "calculate_mortgage"}}]}}'

    async def mock_stream() -> AsyncGenerator[bytes, None]:
        yield payload

    start = time.perf_counter()
    async for _ in validator.validate_stream(mock_stream(), "test_key"):
        pass
    end = time.perf_counter()

    latency_ms = (end - start) * 1000
    # Should definitely be < 1ms for such a tiny payload locally
    assert latency_ms < 1.0


@pytest.mark.asyncio
async def test_pluggable_redis_resolver():
    mock_redis = unittest.mock.AsyncMock()
    # Set up mock to return the byte-encoded JSON when GET is called with the correct key
    mock_redis.get.return_value = b'{"allowed_tools": ["safe_search"], "blocked_tools": ["exec_sql"]}'

    resolver = RedisPolicyResolver(mock_redis)
    validator = RBACValidator(resolver)

    payload = b'{"delta": {"tool_calls": [{"function": {"name": "exec_sql"}}]}}'

    async def mock_stream() -> AsyncGenerator[bytes, None]:
        yield payload

    output_chunks = []
    async for chunk in validator.validate_stream(mock_stream(), "tenant_123"):
        output_chunks.append(chunk)

    mock_redis.get.assert_called_once_with("shield:rbac:tenant_123")

    assert len(output_chunks) == 1
    error_json = orjson.loads(output_chunks[0])
    assert "error" in error_json
    assert error_json["error"]["code"] == "TOOL_ACCESS_FORBIDDEN"
    assert error_json["error"]["tool"] == "exec_sql"
