"""Two ways a value stayed invisible to the detectors: a filler character, or a CJK neighbour.

**Defect 26.** `INVISIBLE_CHARS_PATTERN` stripped one specific set of zero-width
characters. Several other characters render as nothing at all and were left in place,
so a value with one inserted mid-token matched no pattern while the client still
displayed the real thing. NFKC does not remove them either: U+3164 merely folds to
U+1160, which is just as invisible.

**Defect 19.** `CANDIDATE_SECRET_PATTERN` was anchored with `\\b`. A word boundary needs
a word/non-word transition, and CJK ideographs are word characters to Python's Unicode
`re`, so a secret sitting directly against Japanese or Chinese text had no boundary on
either side and Tier 2 never saw it. The same secret with spaces around it was found
immediately.

Scope note on defect 26: `normalize_and_desmuggle` output is what gets forwarded
upstream, not just what gets scanned. `redact_text` returns `working_text`. So anything
added to the strip class is deleted from the user's prompt, and the class here is
limited to characters that are invisible AND have no role in ordinary prose. Variation
selectors (U+FE00-U+FE0F) and the braille blank (U+2800) are deliberately NOT included;
see the test at the bottom, which pins that exclusion so it stays a decision rather than
an oversight.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.pii_engine import normalize_and_desmuggle, pii_engine
from llm_shield_proxy.engines.vault import Vault

EMAIL = "bob@example.com"

# Each of these renders as nothing. Inserted mid-domain they broke every pattern.
INVISIBLE = {
    "U+034F combining grapheme joiner": "\u034f",
    "U+115F hangul choseong filler": "\u115f",
    "U+1160 hangul jungseong filler": "\u1160",
    "U+3164 hangul filler": "\u3164",
    "U+FFA0 halfwidth hangul filler": "\uffa0",
    "U+17B4 khmer vowel inherent aq": "\u17b4",
    "U+061C arabic letter mark": "\u061c",
    "U+2061 function application": "\u2061",
    "U+206A inhibit symmetric swapping": "\u206a",
    "U+E0001 language tag": "\U000e0001",
    "U+E0041 tag latin capital a": "\U000e0041",
}


@pytest.mark.parametrize("name,char", sorted(INVISIBLE.items()))
def test_invisible_char_does_not_hide_an_email(name: str, char: str):
    """The character is stripped before scanning, so the address is redacted.

    Asserted through `redact_text`, which is the request path, rather than through
    `detect_spans`. `detect_spans` scans what it is given and does not normalize;
    normalization is `redact_text`'s first step. Asserting on the raw scan would be
    asserting on something the proxy never actually does.
    """
    text = f"mail bob@exa{char}mple.com now"

    assert char not in normalize_and_desmuggle(text), f"{name} survived normalization"
    assert EMAIL in normalize_and_desmuggle(text)

    redacted = pii_engine.redact_text(text, Vault(synthetic=False))
    assert EMAIL not in redacted, f"{name} hid the address from redaction"
    assert "EMAIL" in redacted


def test_a_stripped_char_does_not_join_two_unrelated_words():
    """Control. Stripping must not silently weld text that was separate.

    The strip class is invisible characters only, so ordinary spacing is untouched.
    """
    assert normalize_and_desmuggle("alpha beta") == "alpha beta"
    assert normalize_and_desmuggle("alpha\nbeta") == "alpha\nbeta"


# --- defect 19 -------------------------------------------------------------------

# Entropy 5.00, comfortably over the 4.5 threshold. Verified detected when
# space-delimited, so the CJK case below isolates the boundary, not the entropy.
SECRET = "xK9mP2qL7vR4nT8wZ5yB3cF6hJ1dG0sA"


def test_control_a_space_delimited_secret_is_found():
    """Without this the CJK test could pass for the wrong reason."""
    assert "SECRET_KEY" in {span[2] for span in pii_engine.detect_spans(f"key {SECRET} end")}


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("密鍵", "です"),  # Japanese: "the secret key is"
        ("密钥", "。"),  # Simplified Chinese plus an ideographic full stop
        ("비밀번호", "입니다"),  # Korean
    ],
)
def test_secret_glued_to_cjk_is_found(prefix: str, suffix: str):
    """CJK characters are word characters, so `\\b` never fired on either side."""
    assert "SECRET_KEY" in {
        span[2] for span in pii_engine.detect_spans(f"{prefix}{SECRET}{suffix}")
    }


def test_an_ordinary_word_is_still_not_a_secret():
    """Control. Widening the boundary must not turn prose into secrets."""
    text = "the quick brown fox jumps over the lazy dog and keeps running onwards"

    assert "SECRET_KEY" not in {span[2] for span in pii_engine.detect_spans(text)}


# --- deliberate exclusions -------------------------------------------------------


@pytest.mark.parametrize("char", ["\ufe0f", "\u2800", "\u180b", "\u180c", "\u180d"])
def test_emoji_and_braille_characters_are_deliberately_preserved(char: str):
    """These are NOT stripped, on purpose, and this pins the decision.

    `normalize_and_desmuggle` output is forwarded upstream, so stripping is not free.
    U+FE0F is emoji presentation: removing it rewrites every emoji in the user's
    prompt. U+2800 is a legitimate blank braille cell. U+180B-U+180D are Mongolian Free
    Variation Selectors, which SELECT GLYPH VARIANTS in ordinary Mongolian text, so
    stripping them rewrote real Mongolian input. All can still hide a value,
    so this is an accepted open gap rather than a closed one, recorded in the fix
    worklog. If that trade is ever revisited, this test is the thing to change.
    """
    assert char in normalize_and_desmuggle(f"mail bob@exa{char}mple.com now")
