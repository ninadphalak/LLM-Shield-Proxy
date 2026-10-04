"""Request fields inside messages, content blocks and input items are scanned by default.

`redact_payload` walked each message by shape: `content`, `name`, `tool_calls`,
`function_call`. Any other field a client replays went to the provider in clear, whatever
the deep switch said, because deep redaction skips `messages` and `input` to avoid
redacting twice. Replayed assistant turns carry exactly such fields: `reasoning_content`,
`refusal`, `audio.transcript`, Anthropic `thinking` blocks and `citations[].cited_text`.

The walk now scans every remaining field and skips an explicit list of identifiers and
provider-verified blobs, the same inversion the reply path already uses.
"""

import base64
import json

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault

EMAIL = "alice@example.com"
SSN = "123-45-6789"


@pytest.fixture
def engine():
    return PIIEngine(enable_tier3=False)


@pytest.fixture(params=[True, False], ids=["deep-on", "deep-off"])
def deep(request, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", request.param)
    return request.param


@pytest.fixture(params=[True, False], ids=["synthetic", "tagged"])
def vault(request):
    return Vault(synthetic=request.param)


def _sent(engine, vault, payload):
    return json.dumps(engine.redact_payload(payload, vault))


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": "ok", "reasoning_content": f"The user is {EMAIL}."},
        {"role": "assistant", "content": None, "refusal": f"I cannot email {EMAIL}."},
        {"role": "assistant", "content": None, "audio": {"id": "audio_1", "transcript": f"Mail {EMAIL}"}},
        {"role": "assistant", "content": [{"type": "refusal", "refusal": f"Not sharing {EMAIL}"}]},
        {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": f"Their SSN is {SSN}", "signature": "c2lnbmF0dXJl"}],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "See the record.",
                    "citations": [{"type": "char_location", "cited_text": f"Contact {EMAIL}", "document_index": 0}],
                }
            ],
        },
    ],
    ids=["reasoning_content", "refusal", "audio.transcript", "refusal-part", "thinking", "citations.cited_text"],
)
def test_replayed_assistant_fields_are_redacted(engine, deep, vault, message):
    sent = _sent(engine, vault, {"messages": [{"role": "user", "content": "hi"}, message]})
    assert EMAIL not in sent
    assert SSN not in sent


def test_tool_result_citations_and_unknown_block_fields_are_redacted(engine, deep, vault):
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": [
                            {"type": "text", "text": "done", "citations": [{"cited_text": f"to {EMAIL}"}]},
                        ],
                    },
                    {"type": "text", "text": "and", "x_vendor_note": f"cc {EMAIL}"},
                ],
            }
        ]
    }
    assert EMAIL not in _sent(engine, vault, payload)


def test_responses_input_items_scan_unknown_fields(engine, deep, vault):
    payload = {
        "input": [
            {"type": "file_search_call", "id": "fs_1", "results": [{"text": f"Owner {EMAIL}", "file_id": "file_1"}]},
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "Z0FBQUFB"},
        ]
    }
    sent = _sent(engine, vault, payload)
    assert EMAIL not in sent


def test_a_long_reasoning_field_is_scanned_whole(engine, deep, vault):
    """Past the blob ceiling only a string's edges are scanned. Replayed reasoning is text."""
    padding = "The model considered the request carefully. " * 400
    message = {"role": "assistant", "content": "ok", "reasoning_content": padding + EMAIL + padding}
    assert len(message["reasoning_content"]) > settings.PAYLOAD_MAX_REDACT_STRING_LENGTH
    assert EMAIL not in _sent(engine, vault, {"messages": [message]})


def test_identifiers_and_verified_blobs_go_out_unchanged(engine, deep, vault):
    """Rewriting an id unlinks a tool result from its call; a signature or encrypted blob is
    checked by the provider byte for byte. High-entropy values are what Tier 2 flags."""
    signature = base64.b64encode(bytes(range(200))).decode()
    image = base64.b64encode(bytes(range(256)) * 4).decode()
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "plan", "signature": signature},
                    {"type": "redacted_thinking", "data": signature},
                    {"type": "tool_use", "id": "toolu_01A9xQz7Lm3Kp", "name": "lookup_customer", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_01A9xQz7Lm3Kp", "content": "found"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
                    {"type": "input_audio", "input_audio": {"data": image, "format": "wav"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_Zx81qPLmN0aBc", "content": "ok"},
        ],
        "input": [{"type": "reasoning", "id": "rs_Q81zLm", "summary": [], "encrypted_content": signature}],
    }
    sent = _sent(engine, vault, payload)
    for opaque in (signature, image, "toolu_01A9xQz7Lm3Kp", "call_Zx81qPLmN0aBc", "rs_Q81zLm", "lookup_customer"):
        assert opaque in sent


def test_replayed_caller_text_stays_restorable(engine, deep):
    vault = Vault(synthetic=False)
    redacted = engine.redact_payload(
        {"messages": [{"role": "assistant", "content": "ok", "reasoning_content": f"User is {EMAIL}"}]}, vault
    )
    token = vault.original_to_token[EMAIL]
    assert token in json.dumps(redacted)
    assert vault.rehydrate(f"Hello {token}") == f"Hello {EMAIL}"


def test_privileged_turn_extra_fields_are_one_way(engine, deep):
    vault = Vault(synthetic=False)
    engine.redact_payload({"messages": [{"role": "system", "content": "rules", "x_note": f"cc {EMAIL}"}]}, vault)
    assert EMAIL in vault.one_way_original_to_token
    assert EMAIL not in vault.token_to_original.values()


def test_an_operator_protected_key_is_still_honoured(engine, deep, vault, monkeypatch):
    monkeypatch.setattr(settings, "PAYLOAD_PROTECTED_KEYS", "reasoning_content")
    message = {"role": "assistant", "content": "ok", "reasoning_content": f"User is {EMAIL}"}
    assert EMAIL in _sent(engine, vault, {"messages": [message]})


def test_nested_text_in_a_privileged_turn_stays_one_way(engine, deep):
    """A tool_result's blocks inside a system turn were walked restorably (review finding)."""
    vault = Vault(synthetic=False)
    nested = [{"type": "text", "text": f"cc {SSN}", "citations": [{"cited_text": f"to {EMAIL}"}]}]
    engine.redact_payload(
        {"messages": [{"role": "system", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": nested}]}]},
        vault,
    )
    for value in (EMAIL, SSN):
        assert value in vault.one_way_original_to_token
        assert value not in vault.token_to_original.values()


def test_an_object_under_an_opaque_key_name_is_still_walked(engine, deep, vault):
    """Only a STRING under `data`, `id` or `name` is opaque; an object there is data (review finding)."""
    message = {"role": "user", "content": "hi", "metadata": {"data": {"customer_email": EMAIL}, "name": {"full": EMAIL}}}
    assert EMAIL not in _sent(engine, vault, {"messages": [message]})


def test_an_inline_image_survives_the_block_policy(engine, deep, vault, monkeypatch):
    """A data: URI is media. Under UNMAPPED_BLOB_POLICY=block the scan must not turn it into a 413."""
    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", "block")
    uri = "data:image/png;base64," + base64.b64encode(bytes(range(256)) * 40).decode()
    payload = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri, "detail": "low"}}]}]}
    assert uri in _sent(engine, vault, payload)


def test_replayed_text_that_starts_like_a_data_uri_is_still_scanned(engine, deep, vault):
    """Round 2: a blanket data: pass-through let `data:,SSN ...` in reasoning go out in clear.

    Replayed text keys are scanned whole whatever the prefix. An unknown field holding a
    data: URI follows the unmapped-blob rule deep redaction already applies to unknown
    top-level fields (forwarded, as media, under the default policy; #72).
    """
    for key in ("reasoning_content", "refusal", "thinking"):
        message = {"role": "assistant", "content": "ok", key: f"data:,SSN {SSN} mail {EMAIL}"}
        sent = _sent(engine, vault, {"messages": [message]})
        assert SSN not in sent
        assert EMAIL not in sent


def test_a_media_url_is_not_rewritten(engine, deep, vault):
    """An image part's URL is a reference; rewriting an email-shaped path breaks it."""
    url = f"https://cdn.example.com/{EMAIL}.png"
    payload = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    assert url in _sent(engine, vault, payload)


def test_media_key_names_deeper_down_are_ordinary_data(engine, deep, vault):
    message = {"role": "user", "content": "hi", "metadata": {"source": {"author": EMAIL}, "image_url": EMAIL}}
    assert EMAIL not in _sent(engine, vault, {"messages": [message]})


def test_opaque_key_names_below_the_direct_fields_are_scanned(engine, deep, vault):
    """Round 3: `name`, `id` or `data` deeper down is ordinary data, a string included."""
    message = {"role": "user", "content": "hi", "metadata": {"name": f"Mail {EMAIL}", "items": [{"id": SSN}]}}
    sent = _sent(engine, vault, {"messages": [message]})
    assert EMAIL not in sent
    assert SSN not in sent


def test_a_source_field_that_is_not_media_is_scanned(engine, deep, vault):
    """Round 3: only a media-typed `source` object is skipped; a text block's `source` string is data."""
    block = {"type": "text", "text": "see", "source": f"SSN {SSN}"}
    other = {"type": "text", "text": "x", "source": {"type": "note", "body": f"to {EMAIL}"}}
    sent = _sent(engine, vault, {"messages": [{"role": "user", "content": [block, other]}]})
    assert SSN not in sent
    assert EMAIL not in sent


def test_an_inline_file_part_survives_the_block_policy(engine, deep, vault, monkeypatch):
    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", "block")
    file_data = "data:application/pdf;base64," + base64.b64encode(bytes(range(256)) * 40).decode()
    part = {"type": "file", "file": {"file_data": file_data, "filename": "report.pdf"}}
    assert file_data in _sent(engine, vault, {"messages": [{"role": "user", "content": [part]}]})


@pytest.mark.parametrize(
    "key, value",
    [
        ("image_url", f"{EMAIL}, SSN {SSN}"),
        ("image_url", {"url": "https://cdn.example.com/a.png", "alt": f"photo of {EMAIL}"}),
        ("input_audio", f"SSN {SSN} {EMAIL}"),
        ("input_audio", {"data": "UklGRg==", "format": "wav", "transcript": f"SSN {SSN} {EMAIL}"}),
        ("file", {"file_id": "file-1", "note": f"SSN {SSN} {EMAIL}"}),
        ("source", {"type": "base64", "data": "UklGRg==", "caption": f"SSN {SSN} {EMAIL}"}),
    ],
    ids=["image_url-text", "image_url-extra-field", "input_audio-text", "input_audio-extra", "file-extra", "source-extra"],
)
def test_a_media_key_with_a_non_media_shape_is_scanned(engine, deep, vault, key, value):
    """Round 4: media is judged by shape, not by key name alone."""
    block = {"type": "text", "text": "hi", key: value}
    sent = _sent(engine, vault, {"messages": [{"role": "user", "content": [block]}]})
    assert SSN not in sent
    assert EMAIL not in sent


def test_a_file_parts_name_is_scanned_but_its_data_is_not(engine, deep, vault):
    file_data = "data:application/pdf;base64,JVBERi0xLjQK"
    part = {"type": "file", "file": {"file_data": file_data, "filename": f"{EMAIL}-statement.pdf"}}
    sent = _sent(engine, vault, {"messages": [{"role": "user", "content": [part]}]})
    assert file_data in sent
    assert EMAIL not in sent
