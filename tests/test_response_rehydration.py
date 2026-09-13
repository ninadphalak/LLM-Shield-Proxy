"""Non-streaming response rehydration: every field the request walk redacts.

The request walk is deliberately broad and the response walk may be narrow -- it only
has to find this session's own tokens. That asymmetry is exactly where a "redacted
outbound, never restored inbound" gap hides, so these tests pin the response side to
the shapes a model actually replies in.
"""

import json

from llm_shield_proxy.api.main import _rehydrate_json_response
from llm_shield_proxy.engines.vault import Vault


def test_anthropic_tool_use_input_is_rehydrated():
    """A `tool_use` block has no `text`, and a walk keyed on that field skipped it.

    The caller then received a placeholder as a tool argument and invoked its tool with
    a value that means nothing outside the vault.
    """
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    response = {
        "type": "message",
        "content": [
            {"type": "text", "text": "Emailing " + token},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "send_email",
                "input": {"to": token, "cc": ["team@example.com", token]},
            },
        ],
    }

    restored = _rehydrate_json_response(response, vault)

    assert restored["content"][0]["text"] == "Emailing sarah@skynet.com"
    assert restored["content"][1]["input"] == {
        "to": "sarah@skynet.com",
        "cc": ["team@example.com", "sarah@skynet.com"],
    }
    assert token not in json.dumps(restored)


def test_anthropic_tool_use_input_is_rehydrated_at_depth():
    """`input` is arbitrary JSON, so nested objects and arrays are walked too."""
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    response = {
        "type": "message",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "book",
                "input": {"a": {"b": {"c": [{"d": token}]}}, "n": 7, "ok": True},
            }
        ],
    }

    restored = _rehydrate_json_response(response, vault)

    assert restored["content"][0]["input"]["a"]["b"]["c"][0]["d"] == "sarah@skynet.com"
    # Non-string leaves keep their types rather than being stringified on the way past.
    assert restored["content"][0]["input"]["n"] == 7
    assert restored["content"][0]["input"]["ok"] is True


def test_openai_tool_arguments_stay_parseable_json():
    """`arguments` is JSON text, so a restored value must be escaped into it.

    Splicing raw turns one wrong field into a parse error on the whole tool call. The
    streaming path escapes; before this change the non-streaming path did not, so the
    same reply behaved differently depending on whether it was streamed.
    """
    hostile = 'Bob "The Man" O\\Brien\nsecond line\twith a tab'
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token(hostile, "PERSON")

    response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"function": {"name": "notify", "arguments": json.dumps({"who": token})}}
                    ],
                }
            }
        ]
    }

    restored = _rehydrate_json_response(response, vault)
    arguments = restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]

    assert json.loads(arguments) == {"who": hostile}


def test_legacy_function_call_arguments_stay_parseable_json():
    """The legacy `function_call.arguments` is the same JSON-text channel."""
    hostile = 'quote " and backslash \\'
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token(hostile, "PERSON")

    response = {
        "choices": [
            {"message": {"function_call": {"name": "f", "arguments": json.dumps({"who": token})}}}
        ]
    }

    restored = _rehydrate_json_response(response, vault)
    arguments = restored["choices"][0]["message"]["function_call"]["arguments"]

    assert json.loads(arguments) == {"who": hostile}


def test_ordinary_values_are_unchanged_by_escaping():
    """A value needing no escaping must round-trip byte-identical.

    Escaping is a correctness fix for a rare input; it must not perturb the common one.
    """
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    response = {
        "choices": [
            {
                "message": {
                    "content": "see " + token,
                    "tool_calls": [
                        {"function": {"name": "f", "arguments": '{"to": "' + token + '"}'}}
                    ],
                }
            }
        ]
    }

    restored = _rehydrate_json_response(response, vault)
    message = restored["choices"][0]["message"]

    assert message["content"] == "see sarah@skynet.com"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"to": "sarah@skynet.com"}'


def test_deeply_nested_tool_input_is_still_rehydrated():
    """A legitimately nested input must not keep placeholders below a shallow cutoff.

    The bound exists to stop a crafted reply becoming an unbounded walk, not to describe
    a real schema. Set inside the range a real payload can occupy, it silently hands the
    caller a placeholder -- the exact failure this walk exists to prevent.
    """
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    # 12 levels of nesting: absurd for a tool schema, trivial for a JSON parser.
    leaf = {"email": token}
    for _ in range(12):
        leaf = {"nested": leaf}

    response = {
        "type": "message",
        "content": [{"type": "tool_use", "id": "t", "name": "deep", "input": leaf}],
    }

    restored = _rehydrate_json_response(response, vault)

    assert token not in json.dumps(restored)
    assert "sarah@skynet.com" in json.dumps(restored)
