"""Regression tests for the October 2026 audit of the catch-all gateway and request walkers.

Each test names the finding it pins. Upstreams are pytest-httpx mocks; all PII is dummy.
"""

from __future__ import annotations

import asyncio
import json
import os

import orjson
import pytest
import yaml
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import UnsafeUpstreamPath, app, build_target_url
from llm_shield_proxy.core.config import request_policy_ctx, settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.stateless_mutation_engine.ast_mutator import StatelessASTVisitor
from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher
from llm_shield_proxy.engines.vault import Vault

client = TestClient(app)

OPENAI_CHAT = "https://api.openai.com/v1/chat/completions"
SSN = "123-45-6789"
CARD = 4111111111111111


@pytest.fixture
def vk(monkeypatch, request):
    """A virtual key unique to the test, so in-memory limiter buckets never carry over."""
    key = f"vk-audit-{request.node.name}"[:60]
    monkeypatch.setattr(settings, "VALID_VIRTUAL_KEYS", key)
    monkeypatch.setattr(settings, "_valid_virtual_keys_set", frozenset({key}))
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(settings, "UPSTREAM_API_KEY", "sk-proxy-global-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_CLOSED")
    monkeypatch.setattr(settings, "ENABLE_FINOPS_METERING", False)
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", False)
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", False)
    return key


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def policies(tmp_path, monkeypatch):
    """Load a policies.yaml for one test and put the previous policy state back after."""
    saved = (settings._flattened_policies, getattr(settings, "_policies_loaded", False), settings._policies_mtime)
    monkeypatch.setattr(settings, "POLICIES_FILE_PATH", str(tmp_path / "policies.yaml"))

    def load(doc: dict) -> None:
        (tmp_path / "policies.yaml").write_text(yaml.safe_dump(doc))
        settings.reload_policies(force=True)

    yield load
    settings._flattened_policies, settings._policies_loaded, settings._policies_mtime = saved
    request_policy_ctx.set({})


@pytest.fixture
def stateless_key(monkeypatch):
    monkeypatch.setattr("llm_shield_proxy.engines.crypto_vault.get_crypto_dek", lambda: b"k" * 32)


# ast_mutator.py:StatelessASTVisitor._detect_pii:raw-text-detection


@pytest.mark.parametrize(
    "value",
    ["contact alice​@example.com", "contact alice＠example.com", "ssn 123​-45-6789"],
)
def test_ast_mutator_detects_on_normalized_text(value):
    visitor = StatelessASTVisitor(StatelessPIICipher(key=os.urandom(32), session_id="s"))
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"arguments": {"q": value}}}
    out, count = asyncio.run(visitor.mutate_counted(orjson.dumps(body)))
    args = orjson.loads(out)["params"]["arguments"]
    assert count >= 1
    assert "_ctx_hash_q" in args
    for fragment in ("alice", "123"):
        if fragment in value:
            assert fragment not in args["q"]


# main.py:_proxy_catch_all_internal:client-selected-jsonrpc-branch-skips-key-controls


def test_jsonrpc_field_does_not_skip_the_blast_radius_limit(httpx_mock, vk, monkeypatch, stateless_key):
    monkeypatch.setattr(settings, "ENABLE_BLAST_RADIUS_LIMITS", True)
    monkeypatch.setattr(settings, "BLAST_RADIUS_BURST_CAPACITY", 5)
    monkeypatch.setattr(settings, "BLAST_RADIUS_REPLENISH_RATE_PER_MIN", 1)
    emails = " ".join(f"user{i}@example.com" for i in range(20))
    body = {"model": "m", "messages": [{"role": "user", "content": emails}], "jsonrpc": "2.0"}

    response = client.post("/v1/chat/completions", headers=_auth(vk), json=body)

    assert response.status_code == 429
    assert httpx_mock.get_requests() == []


def test_jsonrpc_reply_gets_response_pii_redaction(httpx_mock, vk, monkeypatch, stateless_key):
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    httpx_mock.add_response(
        method="POST", url=OPENAI_CHAT,
        json={"choices": [{"message": {"role": "assistant", "content": "model says dave@example.net"}}]},
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "hello"}], "jsonrpc": "2.0"}

    response = client.post("/v1/chat/completions", headers=_auth(vk), json=body)

    assert response.status_code == 200
    assert "dave@example.net" not in response.text


def test_scrub_mode_does_not_take_the_restorable_stateless_branch(httpx_mock, vk, stateless_key):
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, json={"choices": []})
    body = {"model": "m", "messages": [{"role": "user", "content": "contact alice@example.com"}], "jsonrpc": "2.0"}

    client.post("/v1/chat/completions", headers={**_auth(vk), "X-Shield-Masking-Mode": "SCRUB"}, json=body)

    sent = httpx_mock.get_requests()[0].content.decode()
    assert "alice@example.com" not in sent
    assert "_ctx_hash_" not in sent


def test_streamed_jsonrpc_with_response_redaction_takes_the_scanned_path(httpx_mock, vk, monkeypatch, stateless_key):
    """The stateless SSE lexer has no response scan, so such a request uses the normal path."""
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    sse = 'data: {"choices":[{"index":0,"delta":{"content":"model says dave@example.net"}}]}\n\ndata: [DONE]\n\n'
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, text=sse, headers={"content-type": "text/event-stream"})
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "hello alice@example.com"}], "jsonrpc": "2.0"}

    response = client.post("/v1/chat/completions", headers=_auth(vk), json=body)

    assert "_ctx_hash_" not in httpx_mock.get_requests()[0].content.decode()
    assert "dave@example.net" not in response.text


# pii_engine.py:PIIEngine._deep_redact:non-string-leaves-and-keys-unscanned


def test_numbers_and_keys_are_scanned_in_request_walks():
    payload = {
        "model": "m",
        "seed": 123456789,
        "logit_bias": {"50256": -100},
        "metadata": {"customer_card": CARD, "erin@example.com": "vip"},
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "charge",
                 "input": {"card": CARD, "frank@example.com": "x", "count": 3}},
            ]},
        ],
        "prompt": [[1, 2, 3, 99999999]],
    }
    out = pii_engine.redact_payload(payload, Vault(synthetic=False))
    text = json.dumps(out)
    for leaked in (str(CARD), "erin@example.com", "frank@example.com"):
        assert leaked not in text
    # Settings, token ids and small numbers keep their type and value.
    assert out["seed"] == 123456789
    assert out["logit_bias"] == {"50256": -100}
    assert out["prompt"] == [[1, 2, 3, 99999999]]
    assert out["messages"][1]["content"][0]["input"]["count"] == 3
    assert "vip" in out["metadata"].values()


def test_ast_walk_scans_numbers_and_keys():
    visitor = StatelessASTVisitor(StatelessPIICipher(key=os.urandom(32), session_id="s"))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"arguments": {"card": CARD, "bob@example.com": True, "ids": [5551234567, 7], "flag": False}}}
    out, count = asyncio.run(visitor.mutate_counted(orjson.dumps(body)))
    text = out.decode()
    assert str(CARD) not in text and "5551234567" not in text and "bob@example.com" not in text
    assert count >= 3
    args = orjson.loads(out)["params"]["arguments"]
    assert args["flag"] is False and args["ids"][1] == 7


# main.py:_proxy_catch_all_internal:fail-open-on-client-shape-error


def test_unhashable_role_is_scanned_not_a_crash():
    out = pii_engine.redact_payload(
        {"messages": [{"role": ["user"], "content": f"mail alice@example.com ssn {SSN}"}]}, Vault(synthetic=False)
    )
    assert "alice@example.com" not in json.dumps(out)
    out = pii_engine.redact_payload({"input": [{"role": {"x": 1}, "content": f"ssn {SSN}"}]}, Vault(synthetic=False))
    assert SSN not in json.dumps(out)


def test_client_shape_error_is_400_not_a_fail_open_passthrough(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_OPEN")

    def boom(*args, **kwargs):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr("llm_shield_proxy.api.main.pii_engine.redact_payload", boom)
    response = client.post(
        "/v1/chat/completions", headers=_auth(vk),
        json={"model": "m", "messages": [{"role": "user", "content": f"ssn {SSN}"}]},
    )
    assert response.status_code == 400
    assert httpx_mock.get_requests() == []


def test_fail_open_engine_failure_never_forwards_the_raw_body(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_OPEN")

    def boom(*args, **kwargs):
        raise RuntimeError("Simulated NER crash")

    monkeypatch.setattr("llm_shield_proxy.api.main.pii_engine.redact_payload", boom)
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, json={"choices": []})
    response = client.post(
        "/v1/chat/completions", headers=_auth(vk),
        json={"model": "m", "messages": [{"role": "user", "content": f"mail alice@example.com ssn {SSN}"}]},
    )
    assert response.status_code == 200
    sent = httpx_mock.get_requests()[0].content.decode()
    assert "alice@example.com" not in sent and SSN not in sent
    json.loads(sent)


# main.py:_proxy_catch_all_internal:non-post-body-forwarded-unredacted


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "GET"])
def test_non_post_json_body_is_redacted(httpx_mock, vk, method):
    httpx_mock.add_response(method=method, url="https://api.openai.com/v1/some/resource", json={"ok": True})
    body = json.dumps({"metadata": {"note": f"alice.dummy@example.com SSN {SSN}"}})

    response = client.request(method, "/v1/some/resource", headers={**_auth(vk), "Content-Type": "application/json"}, content=body)

    assert response.status_code == 200
    sent = httpx_mock.get_requests()[0].content.decode()
    assert "alice.dummy@example.com" not in sent and SSN not in sent


def test_non_post_non_json_body_is_refused_under_fail_closed(httpx_mock, vk):
    response = client.request("PUT", "/v1/some/resource", headers=_auth(vk), content=f"note {SSN}".encode())
    assert response.status_code == 415
    assert httpx_mock.get_requests() == []


def test_non_post_non_json_body_is_text_redacted_under_fail_open(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_OPEN")
    httpx_mock.add_response(method="PUT", url="https://api.openai.com/v1/some/resource", text="ok")
    client.request("PUT", "/v1/some/resource", headers=_auth(vk), content=f"note {SSN}".encode())
    assert SSN not in httpx_mock.get_requests()[0].content.decode()


# main.py:non-POST-passthrough:response-pii-redaction-skipped


def test_get_retrieval_reply_gets_response_pii_redaction(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    httpx_mock.add_response(
        method="GET", url="https://api.openai.com/v1/responses/resp_1",
        json={"id": "resp_1", "output_text": f"Their SSN is {SSN}"},
    )
    response = client.get("/v1/responses/resp_1", headers=_auth(vk))
    assert response.status_code == 200
    assert SSN not in response.text
    assert response.json()["id"] == "resp_1"


# main.py:_redact_model_originated_json_response:non-dict-root-unscanned


def test_array_root_reply_is_redacted_with_finops_off(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)
    monkeypatch.setattr(settings, "ANONYMOUS_USAGE_TRACKING", False)
    httpx_mock.add_response(
        method="POST", url=OPENAI_CHAT, json=[{"choices": [{"message": {"content": f"Their SSN is {SSN}"}}]}]
    )
    response = client.post("/v1/chat/completions", headers=_auth(vk), json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert SSN not in response.text


# main.py:proxy_key_for_client_chosen_host:scheme-not-bound


@pytest.mark.parametrize("override", ["http://api.openai.com", "https://api.openai.com:8080"])
def test_client_chosen_upstream_must_be_https_on_443_to_get_a_proxy_key(httpx_mock, vk, monkeypatch, override):
    monkeypatch.setattr(settings, "ALLOW_CLIENT_UPSTREAM_OVERRIDE", True)
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://internal-gateway.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai-key")

    async def _public(hostname):
        return True, "162.159.140.245"

    monkeypatch.setattr("llm_shield_proxy.api.main._resolve_and_validate_hostname", _public)
    response = client.post(
        "/v1/chat/completions", headers={**_auth(vk), "X-Upstream-Base-Url": override},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403
    assert httpx_mock.get_requests() == []


def test_client_chosen_fallback_must_be_https_to_get_a_proxy_key(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_CLIENT_UPSTREAM_OVERRIDE", True)
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-deepseek-key", raising=False)

    async def _public(hostname):
        return True, "203.0.113.9"

    monkeypatch.setattr("llm_shield_proxy.api.main._resolve_and_validate_hostname", _public)
    response = client.post(
        "/v1/chat/completions", headers={**_auth(vk), "X-Shield-Fallback-Url": "http://api.deepseek.com"},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403
    assert httpx_mock.get_requests() == []


# main.py:failover:fallback-inherits-client-chosen-provider-key


def test_failover_without_fallback_key_carries_no_proxy_credential(httpx_mock, vk, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RETRY_FAILOVER", True)
    monkeypatch.setattr(settings, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "FALLBACK_BASE_URL", "https://fallback-llm.dummy.test")
    monkeypatch.setattr(settings, "FALLBACK_API_KEY", None)
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, status_code=404)
    httpx_mock.add_response(method="POST", url="https://fallback-llm.dummy.test/v1/chat/completions", json={"choices": []})

    client.post("/v1/chat/completions", headers=_auth(vk), json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]})

    fallback = httpx_mock.get_requests()[-1]
    assert fallback.url.host == "fallback-llm.dummy.test"
    assert "sk-proxy-global-key" not in str(dict(fallback.headers))
    for name in ("authorization", "x-api-key", "x-goog-api-key", "api-key"):
        assert name not in fallback.headers


# main.py:_proxy_catch_all_internal:role-upstream-key-resolved-before-policy-ctx
# main.py:_proxy_catch_all_internal:policy-lookup-by-vkid-not-configured-key


def test_role_upstream_key_is_used_for_a_key_mapped_as_documented(httpx_mock, vk, policies, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai-global")
    policies({
        "roles": {"dept_a": {"upstream_api_key": "sk-role-a"}, "lax": {}},
        "virtual_keys": {vk: "dept_a"},
        "default_role": "lax",
    })
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, json={"choices": []})

    response = client.post("/v1/chat/completions", headers=_auth(vk), json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert httpx_mock.get_requests()[0].headers["authorization"] == "Bearer sk-role-a"


def test_strict_role_mapped_by_raw_key_is_enforced(httpx_mock, vk, policies):
    policies({
        "roles": {"strict_agent_role": {"agent_identity_enforcer": "strict"}, "lax": {}},
        "virtual_keys": {vk: "strict_agent_role"},
        "default_role": "lax",
    })
    response = client.post("/v1/chat/completions", headers=_auth(vk), json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401
    assert httpx_mock.get_requests() == []


# main.py:policy-gate:empty-flattened-policies-disables-fail-closed


def test_empty_policy_map_still_denies_unmapped_keys(httpx_mock, vk, policies):
    policies({"roles": {"r": {}}, "virtual_keys": {}})
    response = client.post("/v1/chat/completions", headers=_auth(vk), json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 403
    assert httpx_mock.get_requests() == []


# main.py:build_target_url-client-path-escapes-operator-base-path (hardening)


@pytest.mark.parametrize(
    "raw_path",
    ["/v1/%2e%2e/%2e%2e/admin/keys", "/%2e%2e%2f%2e%2e%2fadmin%2fkeys%3fx=1", "/v1/chat%23frag", "/v1/%252e%252e/x"],
)
def test_client_path_cannot_leave_the_operator_base_path(httpx_mock, vk, monkeypatch, raw_path):
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "https://upstream.dummy/tenant-a/openai")
    response = client.get(raw_path, headers=_auth(vk))
    assert response.status_code == 400
    assert httpx_mock.get_requests() == []


def test_build_target_url_rejects_dot_segments_and_delimiters():
    assert build_target_url("https://u/base", "v1/chat/completions") == "https://u/base/v1/chat/completions"
    for bad in ("v1/../x", "v1/./x", "a?b=1", "a#b", "a\\b"):
        with pytest.raises(UnsafeUpstreamPath):
            build_target_url("https://u/base", bad)
