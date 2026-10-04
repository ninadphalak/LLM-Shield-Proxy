"""The v3 stateless engine (JSON-RPC bodies) against the guardrail-review findings.

The engine scans every string in the body rather than walking known shapes, so the shapes
the shape walk missed (tool output parts, prompt variables, document blocks, extra_body)
are covered here as long as they appear anywhere in the body. What it does skip is the
JSON-RPC envelope: `jsonrpc`, `method` and `id`. Those names were matched at every depth,
so a tool argument that happened to be called `id` or `method` went out in clear, the same
mistake `redact_payload` once made with `type` and `format`.
"""

import asyncio
import json
import os

import orjson
import pytest

from llm_shield_proxy.engines.stateless_mutation_engine.ast_mutator import StatelessASTVisitor
from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher

EMAIL = "alice@example.com"


def _mutate(body: dict) -> dict:
    visitor = StatelessASTVisitor(StatelessPIICipher(key=os.urandom(32), session_id="s1"))
    return orjson.loads(asyncio.run(visitor.mutate(orjson.dumps(body))))


def _forwarded_text(redacted: dict) -> str:
    """What reaches the upstream, minus the encrypted context fields."""

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not k.startswith("_ctx_hash_") and k != "_shield_ctx"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(redacted))


def test_the_envelope_is_left_alone():
    redacted = _mutate({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "send"}})
    assert redacted["jsonrpc"] == "2.0"
    assert redacted["id"] == 7
    assert redacted["method"] == "tools/call"


@pytest.mark.parametrize("name", ["id", "method", "jsonrpc"])
def test_an_argument_named_like_an_envelope_key_is_redacted(name):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "lookup", "arguments": {name: EMAIL}},
    }
    assert EMAIL not in _forwarded_text(_mutate(body))


def test_shapes_the_shape_walk_missed_are_covered_inside_a_json_rpc_body():
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "arguments": {
                "output": [{"type": "input_text", "text": EMAIL}],
                "code": f"send('{EMAIL}')",
                "variables": {"typed": {"type": "input_text", "text": EMAIL}},
                "document": {"type": "document", "source": {"type": "text", "data": EMAIL}, "title": EMAIL},
                "extra_body": {"system": EMAIL},
            }
        },
    }
    assert EMAIL not in _forwarded_text(_mutate(body))
