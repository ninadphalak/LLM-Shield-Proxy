import orjson
import pytest

from llm_shield_proxy.engines.stateless_mutation_engine.ast_mutator import (
    ASTDepthExceededException,
    StatelessASTVisitor,
)
from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher
from llm_shield_proxy.engines.stateless_mutation_engine.schema_rewriter import DynamicSchemaRewriter


@pytest.fixture
def cipher():
    return StatelessPIICipher(b"0" * 32, version=1)

@pytest.fixture
def mutator(cipher):
    return StatelessASTVisitor(cipher)

@pytest.mark.asyncio
async def test_json_bomb_circuit_breaker(mutator):
    """
    Test 1: Construct a 45-level nested JSON payload and verify the proxy
    halts traversal at depth 40 and raises ASTDepthExceededException.
    """
    payload = {}
    current = payload
    for _ in range(45):
        current["nested"] = {}
        current = current["nested"]

    raw_bytes = orjson.dumps(payload)

    import time
    start = time.perf_counter()
    with pytest.raises(ASTDepthExceededException) as excinfo:
        await mutator.mutate(raw_bytes)
    end = time.perf_counter()

    assert "Depth exceeded 40" in str(excinfo.value)

    # mutate() now offloads traversal to a worker thread via asyncio.to_thread
    # (so a large payload can't block the event loop for every other concurrent
    # request), which trades the previous sub-millisecond bound for a small,
    # constant thread-dispatch overhead. Still asserts the breaker trips fast.
    assert (end - start) < 0.05


@pytest.mark.asyncio
async def test_heterogeneous_array_structural_parity(mutator, cipher):
    """
    Test: Complex Heterogeneous Arrays
    Verify non-sensitive types remain identical, sensitive strings are masked,
    array length is unchanged, and the result parses as JSON.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "arr": ["plain", 42, True, "123-45-6789", {"sub_key": "secret 123-45-6789"}]
        }
    }

    raw_bytes = orjson.dumps(payload)
    mutated_bytes = await mutator.mutate(raw_bytes)
    mutated = orjson.loads(mutated_bytes)

    arr = mutated["params"]["arr"]
    assert len(arr) == 5
    assert arr[0] == "plain"
    assert arr[1] == 42
    assert arr[2] is True

    # String PII mutated to array proxy object
    assert isinstance(arr[3], dict)
    assert arr[3]["_shield_val"] != "[REDACTED]"
    assert isinstance(arr[3]["_shield_val"], str)
    assert len(arr[3]["_shield_val"]) > 0

    # Nested dictionary strings retain their type and receive sibling context.
    assert isinstance(arr[4]["sub_key"], str)
    assert arr[4]["sub_key"] != "[REDACTED]"
    assert "_ctx_hash_sub_key" in arr[4]
    assert cipher.decrypt(arr[4]["_ctx_hash_sub_key"], "sub_key") == "secret 123-45-6789"


@pytest.mark.asyncio
async def test_sibling_injection_and_schema_rewriting(mutator, cipher):
    """
    Test 2: Sibling Injection & Schema Rewriting.
    """
    # Test Schema Rewriter
    schema = {
        "type": "object",
        "properties": {
            "customer_ssn": {
                "type": "string"
            },
            "age": {
                "type": "integer"
            }
        }
    }
    rewritten = DynamicSchemaRewriter.rewrite(schema)
    assert "_ctx_hash_customer_ssn" in rewritten["properties"]
    assert "_ctx_hash_customer_ssn" in rewritten["required"]
    assert "age" not in rewritten.get("required", [])

    # Test Mutator
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "arguments": {
                "customer_ssn": "User ssn is 123-45-6789",
                "age": 30
            }
        }
    }

    raw_bytes = orjson.dumps(payload)
    mutated_bytes = await mutator.mutate(raw_bytes)
    mutated = orjson.loads(mutated_bytes)

    args = mutated["params"]["arguments"]
    assert args["age"] == 30
    assert args["customer_ssn"] != "[REDACTED]"
    assert isinstance(args["customer_ssn"], str)
    assert "_ctx_hash_customer_ssn" in args

    # Verify crypto context binding
    decrypted = cipher.decrypt(args["_ctx_hash_customer_ssn"], "customer_ssn")
    assert decrypted == "User ssn is 123-45-6789"


@pytest.mark.asyncio
async def test_key_immutability_and_stateless(mutator):
    """
    Test 3: Stateless Parity & Key Immutability.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "123-45-6789": "this string is completely safe",
            "safe_key": "here is the secret 123-45-6789"
        }
    }

    raw_bytes = orjson.dumps(payload)
    mutated_bytes = await mutator.mutate(raw_bytes)
    mutated = orjson.loads(mutated_bytes)

    # Keys should remain immutable
    assert "123-45-6789" in mutated["params"]
    assert mutated["params"]["123-45-6789"] == "this string is completely safe"

    # Values containing PII are mutated
    assert isinstance(mutated["params"]["safe_key"], str)
    assert mutated["params"]["safe_key"] != "[REDACTED]"
    assert "_ctx_hash_safe_key" in mutated["params"]


@pytest.mark.asyncio
async def test_reserved_context_field_collision_fails_closed(mutator):
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "email": "person@example.com",
            "_ctx_hash_email": "attacker-controlled",
        },
    }

    with pytest.raises(ValueError, match="Reserved stateless context field"):
        await mutator.mutate(orjson.dumps(payload))


def test_schema_rewriter_finds_nested_tool_schema():
    request = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": ["email"],
                    },
                },
            }
        ]
    }

    rewritten = DynamicSchemaRewriter.rewrite(request)
    parameters = rewritten["tools"][0]["function"]["parameters"]
    assert "_ctx_hash_email" in parameters["properties"]
    assert "_ctx_hash_email" in parameters["required"]
