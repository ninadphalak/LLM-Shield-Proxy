"""Tool definitions on the request path (T1).

A tool's description and its schema's descriptions are caller-authored, static text. They
must reach the provider redacted whatever the deep-redaction switch says, and the values
redacted out of them must NOT come back in the reply: a model that echoes the placeholder
would otherwise pull back text nobody asked to have restored. Values the model can SEND
(`enum`, `const`, `default`, `examples`) stay restorable, because its tool arguments are
rehydrated and must still satisfy the schema.
"""

import json

import pytest

from llm_shield_proxy.core.config import request_policy_ctx, settings
from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault
from llm_shield_proxy.engines.masking import ScrubVault
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

OWNER = "alice.owner@example.com"
CC = "bob.cc@example.com"
LEGACY = "carol.legacy@example.com"


def _payload(messages=None):
    return {
        "messages": messages if messages is not None else [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "escalate",
                    "description": f"Escalate to {OWNER}",
                    "parameters": {
                        "type": "object",
                        "title": f"Routing for {OWNER}",
                        "properties": {"who": {"type": "string", "description": f"cc {CC}"}},
                    },
                },
            }
        ],
        "functions": [{"name": "ping", "description": f"ping {LEGACY}"}],
    }


@pytest.fixture
def engine():
    return PIIEngine(enable_tier3=False)


@pytest.mark.parametrize("deep", [True, False])
def test_tool_definitions_are_redacted_whatever_the_deep_switch_says(engine, deep, monkeypatch):
    """The switch governs fields outside the known shapes. Tool definitions are a known
    shape, so turning it off must not send them in clear."""
    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", deep)
    redacted = engine.redact_payload(_payload(), Vault())
    serialised = json.dumps(redacted)

    for value in (OWNER, CC, LEGACY):
        assert value not in serialised
    assert redacted["tools"][0]["function"]["name"] == "escalate"
    assert redacted["tools"][0]["function"]["parameters"]["type"] == "object"


@pytest.mark.parametrize("synthetic", [True, False])
def test_a_tool_description_is_never_restored_into_the_reply(engine, synthetic):
    vault = Vault(synthetic=synthetic)
    redacted = engine.redact_payload(_payload(), vault)

    function = redacted["tools"][0]["function"]
    placeholders = [
        function["description"].removeprefix("Escalate to "),
        function["parameters"]["title"].removeprefix("Routing for "),
        function["parameters"]["properties"]["who"]["description"].removeprefix("cc "),
        redacted["functions"][0]["description"].removeprefix("ping "),
    ]
    reply = "Contact " + " and ".join(placeholders)
    restored = vault.rehydrate(reply)

    for value in (OWNER, CC, LEGACY):
        assert value not in restored
    assert restored == reply


def test_one_value_gets_one_placeholder_across_a_tool_definition(engine):
    """The description and the title name the same person; the model must see one token."""
    redacted = engine.redact_payload(_payload(), Vault(synthetic=False))
    function = redacted["tools"][0]["function"]
    assert function["description"].removeprefix("Escalate to ") == function["parameters"]["title"].removeprefix(
        "Routing for "
    )


@pytest.mark.parametrize("message_first", [True, False])
def test_a_value_the_caller_also_sent_as_a_message_stays_restorable(engine, message_first):
    """If the caller typed the value themselves, restoring it discloses nothing new. It
    must also be one token, not two, whichever field is walked first."""
    payload = _payload([{"role": "user", "content": f"Is {OWNER} on call?"}])
    if not message_first:
        payload = {"tools": payload["tools"], "messages": payload["messages"]}
    vault = Vault(synthetic=False)
    redacted = engine.redact_payload(payload, vault)

    token = redacted["tools"][0]["function"]["description"].removeprefix("Escalate to ")
    assert token in redacted["messages"][0]["content"]
    assert vault.rehydrate(f"Ask {token}") == f"Ask {OWNER}"


def test_a_later_turn_that_sends_the_value_promotes_it(engine):
    """Order across turns: tool first (one-way), the caller's own message later."""
    vault = Vault(synthetic=False)
    first = engine.redact_payload(_payload(), vault)
    token = first["tools"][0]["function"]["description"].removeprefix("Escalate to ")
    assert vault.rehydrate(token) == token

    second = engine.redact_payload({"messages": [{"role": "user", "content": f"mail {OWNER}"}]}, vault)
    assert second["messages"][0]["content"] == f"mail {token}"
    assert vault.rehydrate(token) == OWNER


def test_one_way_placeholders_never_collide_with_restorable_ones(engine):
    """Tagged tokens are numbered per type. A one-way [EMAIL_1] and a restorable
    [EMAIL_1] would make the reply restore the wrong value."""
    vault = Vault(synthetic=False)
    redacted = engine.redact_payload(_payload([{"role": "user", "content": "mail dave@example.com"}]), vault)

    tokens = {
        redacted["tools"][0]["function"]["description"].removeprefix("Escalate to "),
        redacted["tools"][0]["function"]["parameters"]["properties"]["who"]["description"].removeprefix("cc "),
        redacted["functions"][0]["description"].removeprefix("ping "),
        redacted["messages"][0]["content"].removeprefix("mail "),
    }
    assert len(tokens) == 4
    assert vault.rehydrate(" ".join(sorted(tokens))).count("@example.com") == 1


def test_values_the_model_can_send_stay_restorable(engine):
    """An enum member is a value the model passes back as a tool argument, which is
    rehydrated. One-way here would hand the application a placeholder its enum forbids."""
    vault = Vault(synthetic=False)
    payload = {
        "messages": [],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "notify",
                    "description": "Notify someone",
                    "parameters": {
                        "properties": {
                            "to": {"type": "string", "enum": [OWNER], "default": CC},
                            "cfg": {"const": {"description": LEGACY}},
                        }
                    },
                },
            }
        ],
    }
    redacted = engine.redact_payload(payload, vault)
    properties = redacted["tools"][0]["function"]["parameters"]["properties"]

    arguments = json.dumps(
        {"to": properties["to"]["enum"][0], "cc": properties["to"]["default"], "cfg": properties["cfg"]["const"]}
    )
    restored = json.loads(vault.rehydrate(arguments))
    assert restored == {"to": OWNER, "cc": CC, "cfg": {"description": LEGACY}}


def test_a_schema_property_named_description_is_a_name_not_a_keyword(engine):
    """Under `properties`, `description` is a field the model fills in, not prose."""
    vault = Vault(synthetic=False)
    payload = {
        "messages": [],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "parameters": {"properties": {"description": {"type": "string", "enum": [OWNER]}}},
                },
            }
        ],
    }
    redacted = engine.redact_payload(payload, vault)
    member = redacted["tools"][0]["function"]["parameters"]["properties"]["description"]["enum"][0]
    assert vault.rehydrate(member) == OWNER


def test_one_way_placeholders_survive_a_vault_round_trip(engine):
    """The Redis store persists the vault between turns. Dropping the one-way map there
    would re-mint the value next turn under a new number."""
    vault = Vault(synthetic=False)
    redacted = engine.redact_payload(_payload(), vault)
    token = redacted["tools"][0]["function"]["description"].removeprefix("Escalate to ")

    reloaded = Vault(synthetic=False)
    reloaded.load_state(json.loads(json.dumps(vault.dump_state())))
    assert reloaded.rehydrate(token) == token

    again = engine.redact_payload(_payload(), reloaded)
    assert again["tools"][0]["function"]["description"] == f"Escalate to {token}"


def test_a_stateless_crypto_placeholder_for_a_tool_description_does_not_decrypt(engine):
    """STATELESS_CRYPTO tokens decrypt themselves, so a one-way value cannot be one."""
    vault = StatelessCryptoVault()
    redacted = engine.redact_payload(_payload([{"role": "user", "content": "mail dave@example.com"}]), vault)
    serialised = json.dumps(redacted)

    for value in (OWNER, CC, LEGACY):
        assert value not in serialised
        assert value not in vault.rehydrate(serialised)
    assert "dave@example.com" in vault.rehydrate(serialised), "the caller's own message still restores"


def test_a_vault_without_one_way_support_gets_a_fixed_marker(engine):
    """A duck-typed vault that cannot hold a one-way value must not be handed a restorable
    one instead."""

    class LegacyVault:
        type_counters: dict = {}

        def get_or_create_token(self, original_val, entity_type):
            return "[RESTORABLE]"

        def rehydrate(self, text, retention_length=0):
            return text

    redacted = engine.redact_payload(_payload(), LegacyVault())
    assert "[RESTORABLE]" not in json.dumps(redacted["tools"])
    assert OWNER not in json.dumps(redacted)


def test_scrub_vault_still_scrubs_tool_definitions(engine):
    redacted = engine.redact_payload(_payload(), ScrubVault())
    assert OWNER not in json.dumps(redacted)


def test_an_operator_can_still_send_tools_verbatim(engine, monkeypatch):
    """`tools` in PAYLOAD_PROTECTED_KEYS or a role's payload_skip_keys is an explicit
    promise that the value goes out unchanged."""
    monkeypatch.setattr(settings, "PAYLOAD_PROTECTED_KEYS", "tools")
    assert OWNER in json.dumps(engine.redact_payload(_payload(), Vault())["tools"])
    monkeypatch.setattr(settings, "PAYLOAD_PROTECTED_KEYS", "")

    token = request_policy_ctx.set({"payload_skip_keys": ["functions"]})
    try:
        redacted = engine.redact_payload(_payload(), Vault())
    finally:
        request_policy_ctx.reset(token)
    assert LEGACY in json.dumps(redacted["functions"])
    assert OWNER not in json.dumps(redacted["tools"])


@pytest.mark.parametrize("deep", [True, False])
def test_a_tool_description_past_the_blob_ceiling_is_still_redacted(engine, deep, monkeypatch):
    """The blob ceiling exists so base64 attachments are not scanned. A tool description
    is prose however long it is: treated as a blob, only its edges were inspected and a
    value in the middle went out in clear."""
    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", deep)
    filler = "Routing notes. " * (settings.PAYLOAD_MAX_REDACT_STRING_LENGTH // 15 + 50)
    description = f"{filler}Escalate to {OWNER}. {filler}"
    assert len(description) > settings.PAYLOAD_MAX_REDACT_STRING_LENGTH

    vault = Vault(synthetic=False)
    payload = {"messages": [], "tools": [{"type": "function", "function": {"name": "f", "description": description}}]}
    redacted = engine.redact_payload(payload, vault)
    sent = redacted["tools"][0]["function"]["description"]

    assert OWNER not in sent
    assert sent.startswith(filler)
    assert OWNER not in vault.rehydrate(sent)


@pytest.mark.parametrize("policy", ["skip", "warn"])
def test_a_tool_description_that_looks_like_a_data_uri_is_still_redacted(engine, policy, monkeypatch):
    """`data:` marks a media URI elsewhere and is forwarded unscanned. In a tool
    definition it is only the first word of a sentence."""
    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", policy)
    payload = {
        "messages": [],
        "tools": [{"type": "function", "function": {"name": "f", "description": f"data: records owned by {OWNER}"}}],
    }
    redacted = engine.redact_payload(payload, Vault())
    assert OWNER not in json.dumps(redacted)


def test_tool_definitions_past_the_depth_bound_are_refused(engine):
    """The walk fails closed past its bound instead of forwarding the rest in clear."""
    schema: dict = {"type": "string", "description": f"deep {OWNER}"}
    for _ in range(40):
        schema = {"type": "object", "properties": {"x": schema}}
    payload = {"messages": [], "tools": [{"type": "function", "function": {"name": "f", "parameters": schema}}]}
    with pytest.raises(ValueError, match="nesting depth"):
        engine.redact_payload(payload, Vault())
