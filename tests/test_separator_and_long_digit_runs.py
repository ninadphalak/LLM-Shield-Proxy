"""Two ways structured PII walked past the detector.

**Separators that are not a plain hyphen.** Word and Outlook silently turn a typed
hyphen into an en dash, and PDF extraction produces non-breaking hyphens. An SSN pasted
out of an ordinary document was not recognised at all. This needs no attacker.

**Digit runs longer than a card.** The card pattern accepts 13 to 19 digits and then
asserts that no alphanumeric follows. At 20 digits every candidate length fails that
assertion, so a PAN with an expiry typed after it was not partly redacted, it was missed
entirely. The file's own comment records the same cliff being fixed once by raising the
ceiling from 16 to 19; the cliff simply moved.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine


def _types(text: str) -> set[str]:
    return {span[2] for span in pii_engine.detect_spans(text)}


# U+2010 hyphen, U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en dash,
# U+2014 em dash, U+2212 minus sign. All render as a dash and all appear in real
# documents.
DASHES = ["‐", "‑", "‒", "–", "—", "−"]


@pytest.mark.parametrize("dash", DASHES)
def test_an_ssn_with_a_typographic_dash_is_detected(dash: str) -> None:
    assert "SSN" in _types(f"Member SSN 456{dash}78{dash}9012 on file")


@pytest.mark.parametrize("dash", DASHES)
def test_a_card_with_a_typographic_dash_is_detected(dash: str) -> None:
    assert "CREDIT_CARD" in _types(f"card 4111{dash}1111{dash}1111{dash}1111")


def test_the_plain_ascii_forms_still_work() -> None:
    assert "SSN" in _types("Member SSN 456-78-9012 on file")
    assert "CREDIT_CARD" in _types("card 4111-1111-1111-1111")
    assert "CREDIT_CARD" in _types("card 4111111111111111")


@pytest.mark.parametrize(
    "digits",
    [
        "41111111111111110227",  # 16-digit PAN with an expiry glued on
        "12345678901234567890",  # 20
        "4111111111111111012345",  # 22
    ],
)
def test_a_digit_run_longer_than_a_card_is_still_redacted(digits: str) -> None:
    """Missing it entirely is the worst outcome. Redacting the whole run is the safe one."""
    assert _types(f"PAN {digits}"), f"{len(digits)} digits matched nothing at all"


def test_a_short_digit_run_is_not_swept_up() -> None:
    """Redacting every number would make the proxy unusable."""
    assert "CREDIT_CARD" not in _types("order 12345 shipped")
    assert "CREDIT_CARD" not in _types("the year 2026 and quantity 480")


def test_a_dash_inside_a_word_is_not_treated_as_a_separator() -> None:
    """The boundary assertions must still hold with the wider separator class."""
    assert "SSN" not in _types("build-456-78-9012x")
