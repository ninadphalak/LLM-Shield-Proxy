"""HTML entities hid structured PII from every Tier 1 pattern.

`bob&commat;example.com` matches no email regex. A browser, a chat client, a
markdown renderer and most LLM front-ends all display `bob@example.com`, which is
exactly the gap percent-encoding had before defect 2 closed it.

Three spellings, all of which reached the upstream in clear:

- named, `&commat;` for `@`
- hexadecimal, `&#x40;`
- the whole value encoded character by character

Decimal `&#64;` was caught, but only by accident: `b&#111;b@example.com` still has a
literal `@` in it, so the email pattern matched the `b@example.com` tail. The value
was found for the wrong reason and the local part leaked.

The inspection mirrors the percent-encoding block: find the run, decode it, scan the
decoded text, and redact the whole source run rather than a decoded substring, because
decoding moves offsets and mapping them back is fragile.
"""

from __future__ import annotations

import re

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault

EMAIL = "bob@example.com"

# Request-path vault tokens look like `[EMAIL_1]` / `[ENTITY_OBFUSCATED_PII_1]`.
_PLACEHOLDER = re.compile(r"\[[A-Z0-9_]+_\d+\]")


def _redact(text: str) -> str:
    return pii_engine.redact_text(text, Vault(synthetic=False))


def _entity_encode(value: str) -> str:
    return "".join(f"&#{ord(c)};" for c in value)


@pytest.mark.parametrize(
    "label,encoded",
    [
        ("named entity for @", "bob&commat;example.com"),
        ("hex entity for @", "bob&#x40;example.com"),
        ("uppercase hex entity for @", "bob&#X40;example.com"),
        ("decimal entity for @", "bob&#64;example.com"),
        ("entity in the local part", "b&#111;b&#64;example.com"),
        ("fully encoded", _entity_encode(EMAIL)),
    ],
)
def test_entity_encoded_email_is_redacted(label: str, encoded: str):
    """The decoded value must be found, and the encoded run must not survive.

    Request-path vault tokens are `[<ENTITY_TYPE>_<n>]`, so the assertion is that the
    encoded run is gone and a placeholder took its place, not that the literal string
    "REDACTED" appears. That spelling belongs to the response path.
    """
    redacted = _redact(f"mail {encoded} now")

    assert encoded not in redacted, f"{label}: {encoded} was forwarded untouched"
    assert "commat" not in redacted
    assert "&#" not in redacted
    assert _PLACEHOLDER.search(redacted), f"{label}: nothing was substituted"


def test_entity_encoded_ssn_is_redacted():
    """Not email-specific: any Tier 1 entity is reachable this way."""
    redacted = _redact(f"ssn {_entity_encode('123-45-6789')} now")

    assert "&#" not in redacted
    assert _PLACEHOLDER.search(redacted)


# --- controls --------------------------------------------------------------------


def test_ordinary_entities_in_prose_are_not_flagged():
    """`&amp;` and friends decode to punctuation, which matches no Tier 1 pattern."""
    text = "Tom &amp; Jerry &lt;the cat&gt; chased the mouse &quot;again&quot; today"

    assert not _PLACEHOLDER.search(_redact(text))


def test_text_with_no_entities_is_unchanged():
    """Control. The block must not touch text it has no business touching."""
    text = "the quick brown fox jumps over the lazy dog"

    assert _redact(text) == text


def test_a_plainly_visible_email_beside_entities_is_still_redacted():
    """An address already visible in the raw text must not be missed or mangled."""
    redacted = _redact(f"Tom &amp; Jerry &lt;{EMAIL}&gt; today")

    assert EMAIL not in redacted
    assert _PLACEHOLDER.search(redacted)


def test_a_lone_ampersand_is_not_a_run():
    """A bare `&` is not an entity and must not start an inspection run."""
    text = "sales & marketing discussed the plan at length today"

    assert _redact(text) == text


def test_an_oversized_entity_run_still_scans_its_edges():
    """Greptile P1 on #43: padding past the cap disabled the decoder for the whole run.

    Percent runs edge-scan past their cap and base64 blobs edge-decode past theirs. This
    block skipped the run outright, so the cap was a recipe: pad
    `bob&commat;example.com` with enough non-delimiter characters and raw Tier 1 cannot
    see the encoded address either, so the whole run is forwarded unchanged.
    """
    from llm_shield_proxy.engines.pii_engine import MAX_ENTITY_INSPECTION_CHARS

    padding = "x" * MAX_ENTITY_INSPECTION_CHARS
    leading = "bob&commat;example.com" + padding
    trailing = padding + "%20bob&commat;example.com"
    assert len(leading) > MAX_ENTITY_INSPECTION_CHARS

    assert _PLACEHOLDER.search(_redact(leading)), "value at the head was missed"
    assert _PLACEHOLDER.search(_redact(trailing)), "value at the tail was missed"


def test_an_oversized_run_with_no_pii_is_untouched():
    """Control: edge-decoding must not start flagging ordinary long runs."""
    from llm_shield_proxy.engines.pii_engine import MAX_ENTITY_INSPECTION_CHARS

    text = "a&amp;b" + ("y" * MAX_ENTITY_INSPECTION_CHARS)

    assert not _PLACEHOLDER.search(_redact(text))
