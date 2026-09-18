"""Base64 that is not textbook-standard still has to be decoded and scanned.

`BASE64_CANDIDATE_PATTERN` matched the standard alphabet only, and the decode ran
with `validate=True` on whatever the pattern matched. That left three gaps, each of
which puts plaintext PII on the wire:

- **Unpadded.** `aaa@aaa.com` encodes to `YWFhQGFhYS5jb20`, length 15. Not a multiple
  of 4, so `b64decode(..., validate=True)` raises and the candidate is discarded.
- **URL-safe.** The alphabet substitutes `-` and `_` for `+` and `/`. Neither is in
  the character class, so no candidate is formed at all, padded or not.
- **Double encoded.** One decode pass of a twice-encoded value yields base64, not
  PII, so the tier-1 sweep finds nothing and stops.

Tier 2 entropy does not save any of these. The leaking samples below sit between
2.26 and 4.25 bits, under the 4.5 threshold, precisely because the plaintext they
carry is short and repetitive. High-entropy carriers (a JWT payload segment, for
instance, at 5.08) were already caught; these are the ones that were not.

Every sample here was produced by round-tripping through `base64`, not written by
hand, and each was confirmed to yield zero spans before the fix.
"""

from __future__ import annotations

import base64

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine


def _spans(encoded: str) -> list:
    """Scan an encoded blob the way it would arrive: embedded in ordinary prose."""
    return pii_engine.detect_spans(f"note {encoded} end")


def _assert_caught(encoded: str, plaintext: str) -> None:
    """The blob must be detected, and the fixture must really carry the plaintext."""
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    assert plaintext.encode() in decoded, "fixture does not carry the plaintext it claims"

    assert _spans(encoded), f"{encoded} decodes to {decoded!r} and was not detected"


# --- defect 23, unpadded ---------------------------------------------------------


def test_unpadded_standard_base64_email():
    """`YWFhQGFhYS5jb20`, entropy 3.51. The email reached the upstream in clear."""
    encoded = base64.b64encode(b"aaa@aaa.com").decode().rstrip("=")
    assert len(encoded) % 4 != 0, "sample must be unpadded to exercise the defect"
    _assert_caught(encoded, "aaa@aaa.com")


def test_unpadded_standard_base64_ssn():
    """`MTExLTExLTExMTE`, entropy 2.26. Far below the Tier 2 threshold."""
    encoded = base64.b64encode(b"111-11-1111").decode().rstrip("=")
    _assert_caught(encoded, "111-11-1111")


# --- defect 23, URL-safe ---------------------------------------------------------


def test_urlsafe_base64_padded_email():
    """`YWJvYkBleGFtcGxlLmNvbT8_`, entropy 4.25.

    Padded, so padding is not what defeated it. The `_` simply is not in the
    character class, so no candidate was ever formed.
    """
    encoded = base64.urlsafe_b64encode(b"abob@example.com??").decode()
    assert "_" in encoded or "-" in encoded, "sample must exercise the URL-safe alphabet"
    _assert_caught(encoded, "abob@example.com")


def test_urlsafe_base64_unpadded_email():
    """Both halves of the defect at once."""
    encoded = base64.urlsafe_b64encode(b"abob@example.com>>").decode().rstrip("=")
    assert "-" in encoded or "_" in encoded
    _assert_caught(encoded, "abob@example.com")


# --- defect 29, double encoding --------------------------------------------------


@pytest.mark.parametrize(
    "plaintext",
    [
        "aaa@aaa.com",
        "ssn 123-45-6789",
        "card 4111111111111111",
    ],
)
def test_double_encoded_base64(plaintext: str):
    """One decode yields base64, not PII, so the single pass stopped there."""
    once = base64.b64encode(plaintext.encode())
    twice = base64.b64encode(once).decode()

    assert _spans(twice), f"{twice} double-decodes to {plaintext!r} and was not detected"


# --- controls --------------------------------------------------------------------


def test_ordinary_prose_is_not_flagged():
    """The `>= 6` decoded-length floor and the tier-1 gate still suppress prose."""
    assert not pii_engine.detect_spans("the quick brown fox jumps over the lazy dog")


def test_hyphenated_and_snake_case_words_are_not_flagged():
    """Widening the class to `-` and `_` must not turn ordinary identifiers into hits.

    These now form base64 candidates where they did not before. They must still
    produce no spans: either they fail to decode, or they decode to bytes no tier-1
    pattern matches.
    """
    text = (
        "a well-established-practice in the request_handler_factory module, "
        "see also the long_descriptive_variable_name and check-this-out-later"
    )
    assert not pii_engine.detect_spans(text)


def test_a_blob_that_decodes_to_nothing_interesting_is_not_flagged_as_pii():
    """A blob carrying no PII must not be called BASE64_OBFUSCATED_PII.

    It is still flagged, as SECRET_KEY: at 4.81 bits it is over the Tier 2 entropy
    threshold, and a long high-entropy blob is treated as a possible secret whatever
    it decodes to. That is pre-existing and deliberate, so the assertion is on the
    entity type rather than on emptiness.
    """
    encoded = base64.b64encode(b"the lazy dog sleeps soundly tonight").decode()

    assert "BASE64_OBFUSCATED_PII" not in {span[2] for span in _spans(encoded)}


def test_still_catches_the_standard_padded_case():
    """Regression guard on what already worked."""
    encoded = base64.b64encode(b"bob@example.com and 123-45-6789").decode()
    _assert_caught(encoded, "bob@example.com")
