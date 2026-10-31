"""Regression tests for the independent review of the October 2026 audit fixes.

Each section names the review item it pins. Upstreams are pytest-httpx mocks; all PII
and keys are dummies.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import orjson
import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api import main as main_module
from llm_shield_proxy.api.main import app
from llm_shield_proxy.api.mcp_router import _is_resource_forbidden
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.stateless_mutation_engine.ast_mutator import StatelessASTVisitor
from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.security.egress_guard import EgressPolicyViolationError, find_urls, scan_arguments

client = TestClient(app)

OPENAI_CHAT = "https://api.openai.com/v1/chat/completions"
CARD = 4111111111111111
BOB = "bob.dummy@example.com"
CAROL = "carol.dummy@example.org"


@pytest.fixture
def vk(monkeypatch, request):
    key = f"vk-review-{request.node.name}"[:60]
    monkeypatch.setattr(settings, "VALID_VIRTUAL_KEYS", key)
    monkeypatch.setattr(settings, "_valid_virtual_keys_set", frozenset({key}))
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(settings, "UPSTREAM_API_KEY", "sk-proxy-global-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_CLOSED")
    monkeypatch.setattr(settings, "ENABLE_FINOPS_METERING", False)
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", False)
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", False)
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", False)
    monkeypatch.setattr(settings, "ENABLE_BLAST_RADIUS_LIMITS", False)
    return key


def _auth(key: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {key}", **extra}


@pytest.fixture
def stateless_key(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.engines.crypto_vault.get_crypto_dek", lambda: b"k" * 32)


# --- 1. numeric leaves: only structure-validated detectors; schema numbers never -------

SCHEMA_TOOL = {
    "type": "function",
    "function": {
        "name": "reserve",
        "description": "Reserve capacity",
        "parameters": {
            "type": "object",
            "properties": {
                "units": {"type": "integer", "minimum": 1000000, "maximum": 4294967295, "default": 123456789},
                "kind": {"type": "integer", "enum": [123456789, 987654321]},
            },
        },
    },
}


def test_schema_numbers_and_ids_keep_their_value_and_type():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [SCHEMA_TOOL],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "r", "schema": {"type": "integer", "maximum": 123456789}},
        },
        "metadata": {"created_at": 1730332800, "order_id": 12345678, "phone_like": 5551234567},
        "contents": [
            {"role": "user", "parts": [{"functionResponse": {"name": "lookup", "response": {"order_id": 12345678}}}]}
        ],
    }
    out = pii_engine.redact_payload(json.loads(json.dumps(payload)), Vault(synthetic=False))
    assert out["tools"] == payload["tools"]
    assert out["response_format"] == payload["response_format"]
    assert out["metadata"] == payload["metadata"]
    assert out["contents"] == payload["contents"]


def test_luhn_valid_card_number_as_an_integer_is_still_redacted():
    payload = {
        "model": "m",
        "metadata": {"customer_card": CARD, "ssn_number": 123456789},
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "charge", "input": {"card": CARD, "ts": 1730332800}},
            ]},
        ],
    }
    out = pii_engine.redact_payload(payload, Vault(synthetic=False))
    assert str(CARD) not in json.dumps(out)
    assert "123456789" not in json.dumps(out)  # an SSA-valid SSN shape
    assert out["messages"][1]["content"][0]["input"]["ts"] == 1730332800


def test_detect_number_runs_only_validated_detectors():
    assert pii_engine.detect_number(CARD) == (str(CARD), "CREDIT_CARD")
    assert pii_engine.detect_number(4111111111111112) is None  # Luhn fails
    assert pii_engine.detect_number(1730332800000) is None  # millisecond timestamp
    assert pii_engine.detect_number(5551234567) is None  # phone-shaped
    assert pii_engine.detect_number(666123456) is None and pii_engine.detect_number(900123456) is None
    assert pii_engine.detect_number(True) is None and pii_engine.detect_number(-CARD) is None


def test_ordinary_keys_are_not_renamed_but_pii_keys_are():
    metadata = {
        "2024-10-31": 1,
        "1730332800": "x",
        "5551234567": "y",
        "10.0.0.1": "host",
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08": "sha",
        "erin.dummy@example.com": "vip",
        "4111111111111111": "card",
    }
    out = pii_engine.redact_payload({"model": "m", "metadata": metadata}, Vault(synthetic=False))["metadata"]
    for key in list(metadata)[:5]:
        assert key in out, key
    assert "erin.dummy@example.com" not in out and "4111111111111111" not in out
    assert sorted(map(str, out.values())) == sorted(map(str, metadata.values()))


def test_ast_mutator_scans_numbers_with_validated_detectors_only():
    visitor = StatelessASTVisitor(StatelessPIICipher(key=os.urandom(32), session_id="s"))
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "arguments": {"order_id": 12345678, "ts": 1730332800, "phone": 5551234567, "card": CARD,
                          "1730332800": "k", "dave.dummy@example.net": 1},
            "requestedSchema": {"properties": {"n": {"type": "integer", "maximum": 123456789}}},
        },
    }
    out, count = asyncio.run(visitor.mutate_counted(orjson.dumps(body)))
    params = orjson.loads(out)["params"]
    args = params["arguments"]
    assert args["order_id"] == 12345678 and args["ts"] == 1730332800 and args["phone"] == 5551234567
    assert str(CARD) not in out.decode() and "_ctx_hash_card" in args
    assert "1730332800" in args and "dave.dummy@example.net" not in out.decode()
    assert params["requestedSchema"] == body["params"]["requestedSchema"]
    assert count == 2


def test_proxy_forwards_tool_schema_numbers_unchanged(httpx_mock, vk):
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, json={"choices": []})
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [SCHEMA_TOOL],
        "metadata": {"since": 1730332800, "card": CARD},
    }
    response = client.post("/v1/chat/completions", headers=_auth(vk), json=body)
    assert response.status_code == 200, response.text
    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent["tools"] == [SCHEMA_TOOL]
    assert sent["metadata"]["since"] == 1730332800
    assert sent["metadata"]["card"] != CARD and str(CARD) not in json.dumps(sent)


# --- 2. v3 response scan keeps {_shield_val, _shield_ctx} pairs as a unit ---------------


def _echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=json.loads(request.content))


def test_v3_round_trip_restores_array_elements_with_response_redaction(httpx_mock, vk, monkeypatch, stateless_key):
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    httpx_mock.add_callback(_echo, method="POST", url=OPENAI_CHAT)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "model": "m",
            "params": {"to": BOB, "cc": [CAROL]}}

    response = client.post("/v1/chat/completions", headers=_auth(vk, **{"X-Session-ID": "s-v3"}), json=body)

    assert response.status_code == 200, response.text
    sent = httpx_mock.get_requests()[0].content.decode()
    assert BOB not in sent and CAROL not in sent and "_shield_ctx" in sent
    params = response.json()["params"]
    assert params["to"] == BOB
    assert params["cc"] == [CAROL]


def test_v3_forged_pair_value_is_still_scanned():
    cipher = StatelessPIICipher(key=b"k" * 32, virtual_key_id="vk", session_id="s")
    vault = Vault(synthetic=False)
    real = cipher.encrypt(CAROL, "fake@x.test")
    tree = {"cc": [{"_shield_val": "fake@x.test", "_shield_ctx": real},
                   {"_shield_val": "dave.dummy@example.net", "_shield_ctx": "AAAA"}]}
    out = main_module._redact_v3_model_originated_tree(tree, vault, 0, cipher)
    assert out["cc"][0] == tree["cc"][0]
    assert "dave.dummy@example.net" not in json.dumps(out)


# --- 3. resources/read: decode until stable, refuse traversal and encoded separators ----

POLICY = {"allowed_resources": ["file:///docs/*"]}


@pytest.mark.parametrize(
    "uri",
    [
        "file:///docs/..%2fetc/passwd",
        "file:///docs/%2e%2e%2fetc/passwd",
        "file:///docs/..%5cetc/passwd",
        "file:///docs/%252e%252e/etc/passwd",
        "file:///docs/%25252e%25252e/etc/passwd",
        "file:///docs/a%2fb.txt",
        "file:///docs/..\\etc/passwd",
        "file:///docs/./a.txt",
        "file:///docs/%2E/a.txt",
        "file:///docs/a%00.txt",
        "file:///docs/%ff.txt",
    ],
)
def test_encoded_traversal_is_refused(uri):
    assert _is_resource_forbidden(uri, POLICY)


@pytest.mark.parametrize("uri", ["file:///docs/a.txt", "file:///docs/sub/b%20c.md", "file:///docs/%61.txt"])
def test_ordinary_resource_is_allowed(uri):
    assert not _is_resource_forbidden(uri, POLICY)


def test_decoded_form_is_what_the_pattern_must_match():
    assert _is_resource_forbidden("file:///%64ocs-private/x", {"allowed_resources": ["file:///docs*"]}) is False
    assert _is_resource_forbidden("file:///%64ocs/x", {"allowed_resources": ["file:///internal/*"]})


# --- 4. egress URL finder: prose is not a URL; divergent forms still blocked ------------


async def _public(host):
    return ["93.184.216.34"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "the scheme is https:) and that is all",
        "listen on https:443 for TLS",
        "ports (https:443, http:8080).",
        "see https:\\/\\/api.example.com\\/v1\\/x in the JSON",
        '{"url":"https:\\/\\/api.example.com\\/a"}',
    ],
)
async def test_prose_and_json_escaped_public_urls_pass(text):
    await scan_arguments({"q": text}, None, resolver=_public)


def test_json_escaped_url_is_unescaped_not_dropped():
    assert find_urls("https:\\/\\/api.example.com\\/x") == ["https://api.example.com/x"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http:\\/\\/169.254.169.254\\/latest",
        "http:0/admin",
        "http:2130706433/",
        "http:/169.254.169.254/",
        "http:169.254.169.254/",
        "http:\\\\169.254.169.254/",
        "http:///169.254.169.254/",
        "http://a.example\\@169.254.169.254/",
        "http://93.184.216.34\n@169.254.169.254/",
    ],
)
async def test_divergent_and_internal_forms_stay_blocked(url):
    with pytest.raises(EgressPolicyViolationError):
        await scan_arguments({"url": url}, None, resolver=_public)
