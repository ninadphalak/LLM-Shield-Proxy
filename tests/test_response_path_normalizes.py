"""The response path must see through the same tricks the request path does.

`redact_text` normalizes before scanning. The response path called `detect_spans` on raw
text, so a zero-width space or a fullwidth `@` inside a value passed straight through to
the client. That is reachable whenever the model echoes attacker-supplied content, which
is the ordinary case for retrieval and tool output.

Normalizing shifts character offsets, which is why this was not simply switched on. The
fix scans the raw text first and only falls back to the normalized form when
normalization reveals something the raw scan missed. Text with nothing hidden in it is
returned byte for byte as it arrived.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import redact_model_originated_text

PLAIN = "reach him at bob@example.com"


@pytest.fixture
def vault() -> Vault:
    return Vault(synthetic=False)


def test_control_a_plain_address_is_redacted(vault: Vault) -> None:
    assert "bob@example.com" not in redact_model_originated_text(PLAIN, vault)


@pytest.mark.parametrize(
    "text",
    [
        "reach him at bob@exa​mple.com",  # zero-width space
        "reach him at bob@exa‌mple.com",  # zero-width non-joiner
        "reach him at bob＠example.com",  # fullwidth commercial at
        "reach him at ｂｏb@example.com",  # fullwidth letters
    ],
)
def test_hidden_or_disguised_pii_is_redacted(text: str, vault: Vault) -> None:
    """Checked against the NORMALIZED output, which is what the reader's client renders.

    Asserting on the raw output passes trivially: a zero-width space between `exa` and
    `mple` means the literal string `example.com` is not present, while the address is
    still perfectly readable once the client renders it.
    """
    from llm_shield_proxy.engines.pii_engine import normalize_and_desmuggle

    out = redact_model_originated_text(text, vault)
    assert "example.com" not in normalize_and_desmuggle(out), f"forwarded intact: {out!r}"


def test_ordinary_text_is_returned_unchanged(vault: Vault) -> None:
    """No hidden PII means no rewriting. The client gets exactly what the model wrote."""
    for text in [
        "the quick brown fox",
        "a price of 5 € and a café",  # non-ASCII but nothing hidden
        "code: こんにちは",  # Japanese
        "",
    ]:
        assert redact_model_originated_text(text, vault) == text


def test_a_vault_token_is_still_left_alone(vault: Vault) -> None:
    """The caller's own placeholder must survive to be restored afterwards."""
    token = vault.get_or_create_token("bob@example.com", "EMAIL")
    text = f"write to {token} today"
    assert redact_model_originated_text(text, vault) == text
