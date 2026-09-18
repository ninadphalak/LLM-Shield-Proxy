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


def test_an_obfuscated_copy_is_redacted_even_beside_a_plain_one():
    """Greptile P1 on #42: the reveal test was per-value, so a duplicate defeated it.

    The old condition asked whether a normalized match's VALUE appeared anywhere in the
    raw text. A response carrying both `bob@example.com` and `bob<FULLWIDTH @>example.com`
    made that true for both normalized matches, so the raw-scan result was returned and
    the obfuscated copy went to the client, where it renders as an ordinary address.

    Counting occurrences is what distinguishes the two cases: normalization revealing
    something means strictly MORE matches of a value than the raw scan found, not a value
    the raw scan never saw at all.
    """
    fullwidth = "bob＠example.com"
    out = redact_model_originated_text(
        f"mail bob@example.com and also {fullwidth}", Vault(synthetic=False)
    )

    assert fullwidth not in out
    assert "bob@example.com" not in out
    assert out.count("[EMAIL_REDACTED]") == 2


def test_two_obfuscated_copies_are_both_redacted():
    """Two hidden copies and no plain one; neither may survive."""
    fullwidth = "bob＠example.com"
    out = redact_model_originated_text(f"{fullwidth} and {fullwidth}", Vault(synthetic=False))

    assert fullwidth not in out
    assert out.count("[EMAIL_REDACTED]") == 2
