import orjson
import pytest

from llm_shield_proxy.v3.ast_mutator import ASTDepthExceededException, StatelessASTVisitor
from llm_shield_proxy.v3.crypto import StatelessPIICipher
from llm_shield_proxy.v3.schema_rewriter import DynamicSchemaRewriter


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

    # Assert JSON Bomb circuit breaker trips in < 1.0 ms
    assert (end - start) < 0.001


@pytest.mark.asyncio
async def test_heterogeneous_array_structural_parity(mutator):
    """
    Test: Complex Heterogeneous Arrays
    Verify non-sensitive types remain identical, sensitive strings are masked, 
    array length is unchanged, and JSON syntax is 100% valid.
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
    assert arr[3]["_shield_val"] == "[REDACTED]"

    # Nested dictionary in array mutated
    assert isinstance(arr[4]["sub_key"], dict)
    assert arr[4]["sub_key"]["_shield_val"] == "[REDACTED]"


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
    assert "_shield_val" in args["customer_ssn"]
    assert args["customer_ssn"]["_shield_val"] == "[REDACTED]"
    assert "_shield_ctx" in args["customer_ssn"]

    # Verify crypto context binding
    decrypted = cipher.decrypt(args["customer_ssn"]["_shield_ctx"], "$.params.arguments.customer_ssn")
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
    assert isinstance(mutated["params"]["safe_key"], dict)
    assert mutated["params"]["safe_key"]["_shield_val"] == "[REDACTED]"
