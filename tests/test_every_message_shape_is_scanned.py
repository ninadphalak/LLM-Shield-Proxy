"""Every value inside a chat message is scanned, whatever shape it has.

`redact_payload` walks `messages` by shape, and deep redaction skips `messages` so nothing
is redacted twice. Anything the walk did not recognise therefore reached the provider in
clear. These tests cover the real provider shapes that hit that gap, and the rule for
everything else: a value of an unexpected type is scanned, never passed through.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

client = TestClient(app)

SSN = "123-45-6789"
EMAIL = "jane.doe@example.com"
OPENAI_CHAT = "https://api.openai.com/v1/chat/completions"


@pytest.fixture
def engine():
    return PIIEngine(enable_tier3=False)


# Real provider shapes a client replays in conversation history.
REAL_SHAPES = {
    # OpenAI Chat Completions custom (free-form) tool call: the text is in `custom.input`.
    "openai-custom-tool-call": {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "custom", "custom": {"name": "notes", "input": f"ssn {SSN}"}}],
    },
    # Anthropic server tool call (web search, code execution).
    "anthropic-server-tool-use": {
        "role": "assistant",
        "content": [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": f"ssn {SSN}"}}],
    },
    # Anthropic MCP connector call.
    "anthropic-mcp-tool-use": {
        "role": "assistant",
        "content": [
            {"type": "mcp_tool_use", "id": "mcptoolu_1", "name": "lookup", "server_name": "crm", "input": {"email": EMAIL}}
        ],
    },
}

# Shapes a provider would reject, but only after it has received the body.
UNEXPECTED_SHAPES = {
    "string-inside-content-list": {"role": "user", "content": [f"my ssn is {SSN}"]},
    "content-as-single-object": {"role": "user", "content": {"type": "text", "text": f"my ssn is {SSN}"}},
    "tool-result-content-object": {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": {"type": "text", "text": f"ssn {SSN}"}}],
    },
    "string-inside-tool-result-list": {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [f"ssn {SSN}"]}],
    },
    "unknown-field-on-a-tool-call": {
        "role": "assistant",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}, "extra": f"ssn {SSN}"}],
    },
    "tool-calls-as-single-object": {
        "role": "assistant",
        "tool_calls": {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": f"ssn {SSN}"}},
    },
    "tool-call-arguments-as-object": {
        "role": "assistant",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": {"ssn": SSN}}}],
    },
    "legacy-function-call-arguments-as-object": {
        "role": "assistant",
        "content": None,
        "function_call": {"name": "f", "arguments": {"ssn": SSN}},
    },
    "legacy-function-call-as-string": {"role": "assistant", "function_call": f"ssn {SSN}"},
}


@pytest.mark.parametrize("message", list(REAL_SHAPES.values()), ids=list(REAL_SHAPES))
def test_replayed_tool_calls_of_every_kind_reach_the_provider_redacted(httpx_mock, message):
    httpx_mock.add_response(method="POST", url=OPENAI_CHAT, json={"choices": [{"message": {"content": "ok"}}]})

    client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-abc123"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}, message]},
    )

    sent = httpx_mock.get_requests()[0].content.decode("utf-8")
    assert SSN not in sent
    assert EMAIL not in sent


@pytest.mark.parametrize("message", list(UNEXPECTED_SHAPES.values()), ids=list(UNEXPECTED_SHAPES))
def test_a_message_shape_the_walk_does_not_know_is_still_scanned(engine, message):
    redacted = engine.redact_payload({"messages": [message]}, Vault())

    assert SSN not in json.dumps(redacted)


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [f"my ssn is {SSN}"]},
        {"messages": {"role": "user", "content": f"my ssn is {SSN}"}},
        {"system": {"text": f"ops ssn {SSN}"}},
        {"instructions": {"text": f"ops ssn {SSN}"}},
        {"input": {"role": "user", "content": f"ssn {SSN}"}},
        {"prompt": [{"text": f"ssn {SSN}"}]},
        {"input": [{"type": "function_call", "call_id": "c1", "name": "f", "arguments": {"ssn": SSN}}]},
        {"input": [{"type": "function_call_output", "call_id": "c1", "output": {"ssn": SSN}}]},
    ],
    ids=[
        "messages-string-item",
        "messages-object",
        "system-object",
        "instructions-object",
        "input-object",
        "prompt-object-item",
        "responses-arguments-object",
        "responses-output-object",
    ],
)
def test_a_targeted_key_of_an_unexpected_type_is_still_scanned(engine, payload):
    redacted = engine.redact_payload(payload, Vault())

    assert SSN not in json.dumps(redacted)


def test_the_shapes_are_kept(engine):
    """Scanning an unexpected value must not change its type: the provider decides."""
    payload = {
        "messages": [
            {"role": "user", "content": {"type": "text", "text": f"ssn {SSN}"}},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": {"type": "text", "text": "x"}}]},
            REAL_SHAPES["openai-custom-tool-call"],
        ]
    }
    redacted = engine.redact_payload(payload, Vault())

    assert isinstance(redacted["messages"][0]["content"], dict)
    assert isinstance(redacted["messages"][1]["content"][0]["content"], dict)
    call = redacted["messages"][2]["tool_calls"][0]
    assert call["id"] == "call_1" and call["type"] == "custom" and call["custom"]["name"] == "notes"


def test_a_value_in_a_replayed_tool_call_is_restored_in_the_reply(engine):
    """Caller and model values stay restorable, like `function.arguments` already is."""
    vault = Vault(synthetic=False)
    engine.redact_payload({"messages": [REAL_SHAPES["anthropic-server-tool-use"]]}, vault)

    token = vault.original_to_token[SSN]
    assert vault.rehydrate(f"found {token}") == f"found {SSN}"


def test_token_ids_in_a_prompt_list_pass_through(engine):
    redacted = engine.redact_payload({"prompt": [[101, 2023, 102], "plain"]}, Vault())

    assert redacted["prompt"][0] == [101, 2023, 102]
