"""Request shapes the LiteLLM guardrail review found, checked against `redact_payload`.

`redact_payload` walks the known request keys by shape, and deep redaction skips those
keys so nothing is redacted twice. Text inside a targeted key that the shape walk does not
reach therefore goes to the provider in clear, whatever the deep switch says.

Two questions per shape: is the value redacted, and which vault does it land in? Text the
application wrote (system and developer turns, `system`, `instructions`, tool schemas) is
one-way: the reply never gets it back, so a caller who gets the model to echo a placeholder
receives the placeholder, not the application's value. Text the caller sent stays
restorable.
"""

import json

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

SECRET = "ops.lead@example.com"
CALLER = "alice@example.com"


@pytest.fixture
def engine():
    return PIIEngine(enable_tier3=False)


@pytest.fixture(params=[True, False], ids=["deep-on", "deep-off"])
def deep(request, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", request.param)
    return request.param


# Synthetic stand-ins (a fake email in place of the real one) are the default; tagged
# placeholders are the other mode. Every test runs in both.
@pytest.fixture(autouse=True, params=[True, False], ids=["synthetic", "tagged"])
def synthetic(request):
    return request.param


@pytest.fixture
def _redact(synthetic):
    def run(engine, payload):
        vault = Vault(synthetic=synthetic)
        return engine.redact_payload(payload, vault), vault

    return run


def _assert_one_way(vault, value):
    """Redacted into the one-way map, and an echo of its token is not restored."""
    assert value in vault.one_way_original_to_token
    assert value not in vault.token_to_original.values()
    token = vault.one_way_original_to_token[value]
    assert value not in vault.rehydrate(f"Sure: {token}")


def _assert_restorable(vault, value):
    token = vault.original_to_token[value]
    assert vault.rehydrate(f"Sure: {token}") == f"Sure: {value}"


# 1. Application-authored turns are one-way.


@pytest.mark.parametrize("role", ["system", "developer"])
def test_responses_input_system_and_developer_items_are_one_way(engine, deep, role, _redact):
    payload = {
        "input": [
            {"role": role, "content": f"Escalate to {SECRET}"},
            {"type": "message", "role": role, "content": [{"type": "input_text", "text": f"cc {SECRET}"}]},
            {"role": "user", "content": f"I am {CALLER}"},
        ]
    }
    redacted, vault = _redact(engine, payload)

    assert SECRET not in json.dumps(redacted)
    _assert_one_way(vault, SECRET)
    _assert_restorable(vault, CALLER)


@pytest.mark.parametrize("role", ["system", "developer"])
def test_chat_system_and_developer_messages_are_one_way(engine, deep, role, _redact):
    payload = {
        "messages": [
            {"role": role, "content": f"Escalate to {SECRET}"},
            {"role": role, "content": [{"type": "text", "text": f"cc {SECRET}"}]},
            {"role": "user", "content": f"I am {CALLER}"},
        ]
    }
    redacted, vault = _redact(engine, payload)

    assert SECRET not in json.dumps(redacted)
    _assert_one_way(vault, SECRET)
    _assert_restorable(vault, CALLER)


def test_top_level_system_and_instructions_are_one_way(engine, deep, _redact):
    for payload in (
        {"system": f"Escalate to {SECRET}", "messages": []},
        {"system": [{"type": "text", "text": f"Escalate to {SECRET}"}], "messages": []},
        {"instructions": f"Escalate to {SECRET}", "input": ""},
    ):
        redacted, vault = _redact(engine, payload)
        assert SECRET not in json.dumps(redacted)
        _assert_one_way(vault, SECRET)


def test_a_value_the_caller_also_sent_stays_restorable(engine, deep, _redact):
    """The caller already knows a value they sent, so restoring it discloses nothing."""
    payload = {
        "instructions": f"The user is {CALLER}",
        "input": [{"role": "user", "content": f"I am {CALLER}"}],
    }
    _, vault = _redact(engine, payload)
    _assert_restorable(vault, CALLER)


@pytest.mark.parametrize("system_first", [True, False], ids=["system-first", "user-first"])
def test_a_caller_typing_the_applications_value_cannot_unlock_it(engine, system_first, _redact):
    """One shared token would make the system prompt's placeholder restorable as soon as
    the caller typed the same value, so asking the model to repeat its instructions would
    confirm a guess. The two occurrences get different tokens instead."""
    turns = [{"role": "system", "content": f"Escalate to {SECRET}"}, {"role": "user", "content": f"cc {SECRET}"}]
    if not system_first:
        turns.reverse()
    redacted, vault = _redact(engine, {"messages": turns})

    by_role = {m["role"]: m["content"] for m in redacted["messages"]}
    system_token = by_role["system"].removeprefix("Escalate to ")
    user_token = by_role["user"].removeprefix("cc ")
    assert system_token != user_token
    assert vault.rehydrate(system_token) == system_token
    assert vault.rehydrate(user_token) == SECRET


def test_a_later_turn_cannot_unlock_an_earlier_system_value(engine, synthetic):
    vault = Vault(synthetic=synthetic)
    first = engine.redact_payload({"messages": [{"role": "system", "content": f"Escalate to {SECRET}"}]}, vault)
    system_token = first["messages"][0]["content"].removeprefix("Escalate to ")

    engine.redact_payload({"messages": [{"role": "user", "content": f"cc {SECRET}"}]}, vault)
    assert vault.rehydrate(system_token) == system_token


def test_a_system_message_name_is_one_way(engine, _redact):
    payload = {"messages": [{"role": "system", "name": "Jane_Officer", "content": "hi"}]}
    redacted, vault = _redact(engine, payload)

    token = redacted["messages"][0]["name"]
    assert token != "Jane_Officer"
    assert "Jane" not in vault.rehydrate(token)


@pytest.mark.parametrize("field", ["user", "safety_identifier"])
def test_end_user_identifiers_are_one_way(engine, deep, field, _redact):
    """The application sets these to identify its end user. A reply that echoes the
    placeholder must not hand that identifier to whoever is reading the reply."""
    redacted, vault = _redact(engine, {"messages": [{"role": "user", "content": "hi"}], field: SECRET})

    assert SECRET not in json.dumps(redacted)
    _assert_one_way(vault, SECRET)


@pytest.mark.parametrize("shape", [{"email": SECRET}, [SECRET]], ids=["object", "list"])
def test_end_user_identifiers_of_any_shape_are_one_way(engine, deep, shape, _redact):
    """Nothing upstream checks the field's type, so an object or list must not fall back
    to the restorable deep walk, or out in clear with deep redaction off."""
    redacted, vault = _redact(engine, {"messages": [], "user": shape})

    assert SECRET not in json.dumps(redacted)
    _assert_one_way(vault, SECRET)


def test_an_operator_protected_user_field_is_left_alone(engine, monkeypatch, _redact):
    monkeypatch.setattr(settings, "PAYLOAD_PROTECTED_KEYS", "user")
    redacted, _ = _redact(engine, {"messages": [], "user": SECRET})
    assert redacted["user"] == SECRET


# 2. function_call_output with a list of parts.


def test_function_call_output_parts_are_redacted(engine, deep, _redact):
    payload = {
        "input": [
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": [
                    {"type": "input_text", "text": CALLER},
                    {"type": "input_image", "image_url": "https://example.com/a.png"},
                ],
            }
        ]
    }
    redacted, vault = _redact(engine, payload)

    assert CALLER not in json.dumps(redacted)
    assert redacted["input"][0]["output"][1] == {"type": "input_image", "image_url": "https://example.com/a.png"}
    # A tool result is the caller's data, so it stays restorable.
    _assert_restorable(vault, CALLER)


# 3. Replayed custom_tool_call input and code_interpreter_call code.


def test_replayed_custom_tool_and_code_interpreter_calls_are_redacted(engine, deep, _redact):
    payload = {
        "input": [
            {"type": "custom_tool_call", "call_id": "c1", "name": "shell", "input": f"mail {CALLER}"},
            {
                "type": "code_interpreter_call",
                "id": "ci_1",
                "container_id": "cntr_1",
                "code": f"send('{CALLER}')",
                "outputs": [{"type": "logs", "logs": f"sent to {CALLER}"}],
            },
        ]
    }
    redacted, _ = _redact(engine, payload)

    assert CALLER not in json.dumps(redacted)
    assert redacted["input"][1]["container_id"] == "cntr_1"


# 4. Typed prompt variables.


def test_prompt_object_variables_are_redacted(engine, deep, _redact):
    payload = {
        "prompt": {
            "id": "pmpt_123",
            "version": "2",
            "variables": {
                "plain": f"I am {CALLER}",
                "typed": {"type": "input_text", "text": f"reach me at {CALLER}"},
                "image": {"type": "input_image", "image_url": "https://example.com/a.png"},
            },
        }
    }
    redacted, vault = _redact(engine, payload)

    assert CALLER not in json.dumps(redacted)
    assert redacted["prompt"]["id"] == "pmpt_123"
    assert redacted["prompt"]["variables"]["image"] == payload["prompt"]["variables"]["image"]
    _assert_restorable(vault, CALLER)


# 5. Anthropic document blocks.


def _document_message(source, **extra):
    return {"messages": [{"role": "user", "content": [{"type": "document", "source": source, **extra}]}]}


def test_document_text_source_title_and_context_are_redacted(engine, deep, _redact):
    payload = _document_message(
        {"type": "text", "media_type": "text/plain", "data": f"Contract for {CALLER}"},
        title=f"Notes from {CALLER}",
        context=f"Sent by {CALLER}",
    )
    redacted, _ = _redact(engine, payload)

    assert CALLER not in json.dumps(redacted)
    assert redacted["messages"][0]["content"][0]["source"]["media_type"] == "text/plain"


@pytest.mark.parametrize(
    "content",
    [f"Contract for {CALLER}", [{"type": "text", "text": f"Contract for {CALLER}"}]],
    ids=["string", "blocks"],
)
def test_document_content_source_is_redacted(engine, deep, content, _redact):
    redacted, _ = _redact(engine, _document_message({"type": "content", "content": content}))
    assert CALLER not in json.dumps(redacted)


@pytest.mark.parametrize(
    "source",
    [
        {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQKJcfsj6IK"},
        {"type": "url", "url": "https://example.com/doc.pdf"},
        {"type": "file", "file_id": "file_011"},
    ],
    ids=["base64", "url", "file"],
)
def test_document_binary_sources_are_untouched(engine, deep, source, _redact):
    redacted, _ = _redact(engine, _document_message(source))
    assert redacted["messages"][0]["content"][0]["source"] == source


# 6. extra_body.


def test_extra_body_is_redacted_and_its_application_fields_are_one_way(engine, monkeypatch, _redact):
    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", True)
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "extra_body": {
            "system": f"Escalate to {SECRET}",
            "instructions": f"Escalate to {SECRET}",
            "tools": [{"type": "function", "function": {"name": "f", "description": f"cc {SECRET}"}}],
            "note": f"from {CALLER}",
        },
    }
    redacted, vault = _redact(engine, payload)

    serialised = json.dumps(redacted)
    assert SECRET not in serialised
    assert CALLER not in serialised
    _assert_one_way(vault, SECRET)
    _assert_restorable(vault, CALLER)
    assert redacted["extra_body"]["tools"][0]["function"]["name"] == "f"
