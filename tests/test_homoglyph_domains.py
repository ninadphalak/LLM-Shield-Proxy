"""A Cyrillic look-alike in a domain hid the whole address.

`bob@ex\\u0430mple.com` carries CYRILLIC SMALL LETTER A where the Latin `a` belongs. It
renders identically in every client and matches no email pattern, so the address was
forwarded in clear. NFKC does not touch it, and should not: Cyrillic `a` and Latin `a`
are distinct characters, not compatibility variants of one another.

The fix scans a FOLDED COPY and reports spans against the ORIGINAL text. That works
only because the UTS #39 table vendored here maps one codepoint to one ASCII character,
so folding never changes a string's length and offsets carry over unchanged. The
engine asserts that rather than assuming it.

What the fix deliberately does NOT do is fold the text that gets forwarded.
`redact_text` returns its working text, so folding Cyrillic to Latin there would rewrite
genuine Russian prose into nonsense. Only the matched span is replaced; every other
character reaches the provider exactly as sent. The Russian-prose test below pins that.
"""

from __future__ import annotations

import pathlib

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault

CYRILLIC_A = "а"
CYRILLIC_IE = "е"
CYRILLIC_ER = "р"
CYRILLIC_ES = "с"
CYRILLIC_O = "о"


def _types(text: str) -> set:
    return {span[2] for span in pii_engine.detect_spans(text)}


def _redact(text: str) -> str:
    return pii_engine.redact_text(text, Vault(synthetic=False))


@pytest.mark.parametrize(
    "label,address",
    [
        ("Cyrillic a in the domain", f"bob@ex{CYRILLIC_A}mple.com"),
        ("Cyrillic e in the domain", f"bob@exampl{CYRILLIC_IE}.com"),
        ("Cyrillic o in the local part", f"b{CYRILLIC_O}b@example.com"),
        ("Cyrillic c in the TLD", f"bob@example.{CYRILLIC_ES}om"),
        (
            "several at once",
            f"b{CYRILLIC_O}b@ex{CYRILLIC_A}mpl{CYRILLIC_IE}.com",
        ),
    ],
)
def test_homoglyph_email_is_detected(label: str, address: str):
    """The folded copy matches, and the span lands on the original characters."""
    assert "EMAIL" in _types(f"mail {address} now"), f"{label}: {address!r} leaked"


def test_the_original_characters_are_what_get_replaced():
    """The span must cover the real source text, not the folded spelling."""
    address = f"bob@ex{CYRILLIC_A}mple.com"
    text = f"mail {address} now"

    hits = [span for span in pii_engine.detect_spans(text) if span[2] == "EMAIL"]
    assert hits
    start, end, _entity, matched = hits[0]
    assert text[start:end] == matched, "span and matched text disagree"
    assert CYRILLIC_A in matched, "matched text was the folded spelling, not the original"


def test_the_address_does_not_survive_redaction():
    address = f"bob@ex{CYRILLIC_A}mple.com"

    assert address not in _redact(f"mail {address} now")


# --- controls --------------------------------------------------------------------


def test_russian_prose_is_forwarded_unchanged():
    """The whole reason folding happens on a copy and not on the payload.

    Folding the forwarded text would turn these words into Latin gibberish.
    """
    russian = "Привет, как дела сегодня"

    assert _redact(russian) == russian


def test_ascii_prose_is_unaffected():
    text = "the quick brown fox jumps over the lazy dog"

    assert _redact(text) == text


def test_a_plain_ascii_address_still_works():
    """Regression guard on the ordinary path."""
    assert "EMAIL" in _types("mail bob@example.com now")


# --- drift guard -----------------------------------------------------------------


def test_the_two_vendored_tables_are_identical():
    """The proxy and the benchmark each carry a copy; they must not drift.

    `pii-leak-benchmark` is a standalone distribution, so neither package can import
    the other. `scripts/build_confusables.py` writes both from one source. If this
    fails, one copy was edited or regenerated alone.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    proxy = root / "llm_shield_proxy" / "engines" / "confusables.py"
    benchmark = root / "pii-leak-benchmark" / "pii_leak_benchmark" / "confusables.py"

    assert proxy.read_bytes() == benchmark.read_bytes()


def test_the_fold_never_changes_length():
    """The property the whole offset-preservation argument rests on."""
    from llm_shield_proxy.engines.confusables import CONFUSABLE_TO_ASCII

    assert CONFUSABLE_TO_ASCII
    for source, folded in CONFUSABLE_TO_ASCII.items():
        assert len(source) == 1, f"{source!r} is not a single codepoint"
        assert len(folded) == 1, f"{source!r} folds to {folded!r}, which is not one character"
        assert folded.isascii()
