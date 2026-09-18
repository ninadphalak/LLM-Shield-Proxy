"""An oversized base64 blob had PII at its head or tail and nobody looked.

Invariant 6 says candidates over `MAX_BASE64_INSPECTION_CHARS` "get 256-char boundary
guards". They did, but only as PLAINTEXT: the guard regions were left in the tier-1
scan segments and never decoded. A text detector does not match base64, so the guards
guarded against nothing encoded. A 12,000-character attachment whose first bytes are
`bob@example.com ` produced zero spans.

Decoding the guards is cheap and it works: a 256-character prefix of a blob is
4-aligned by construction and decodes to exactly 192 bytes. The tail guard is aligned
back to the blob's own framing so it decodes too rather than yielding shifted garbage.

The interior is still not decoded, deliberately. That is the measured bound invariant 6
exists to hold, and this change does not touch it.
"""

from __future__ import annotations

import base64

import pytest

from llm_shield_proxy.engines.pii_engine import (
    BASE64_BOUNDARY_SCAN_CHARS,
    MAX_BASE64_INSPECTION_CHARS,
    pii_engine,
)

EMAIL = "bob@example.com"
FILLER = b"A" * 9000


def _oversized(payload: bytes) -> str:
    blob = base64.b64encode(payload).decode()
    assert len(blob) > MAX_BASE64_INSPECTION_CHARS, "fixture must exceed the inspection cap"
    return blob


def _entity_types(text: str) -> set:
    return {span[2] for span in pii_engine.detect_spans(text)}


def test_pii_at_the_head_of_an_oversized_blob_is_found():
    """The leading guard decodes to 192 bytes, which is where the address sits."""
    blob = _oversized(EMAIL.encode() + b" " + FILLER)

    assert "BASE64_OBFUSCATED_PII" in _entity_types(f"attachment {blob} end")


def test_pii_at_the_tail_of_an_oversized_blob_is_found():
    """The trailing guard is aligned to the blob's framing, so it decodes cleanly."""
    blob = _oversized(FILLER + b" " + EMAIL.encode())

    assert "BASE64_OBFUSCATED_PII" in _entity_types(f"attachment {blob} end")


def test_the_redaction_covers_the_guard_that_matched():
    """The span must line up with the text it matched, so rehydration stays exact."""
    blob = _oversized(EMAIL.encode() + b" " + FILLER)
    text = f"attachment {blob} end"

    hits = [span for span in pii_engine.detect_spans(text) if span[2] == "BASE64_OBFUSCATED_PII"]
    assert hits
    start, end, _entity, matched = hits[0]
    assert text[start:end] == matched, "span and matched text disagree"
    assert len(matched) <= BASE64_BOUNDARY_SCAN_CHARS


def test_the_interior_is_still_not_decoded():
    """Control. Invariant 6's bound is the point; this change must not remove it.

    The address sits in the middle of the blob, far from either guard, and must stay
    undetected. If this ever starts passing, the cost bound has been quietly dropped.
    """
    middle = FILLER + EMAIL.encode() + FILLER
    blob = _oversized(middle)
    head = blob[:BASE64_BOUNDARY_SCAN_CHARS]
    tail = blob[len(blob) - BASE64_BOUNDARY_SCAN_CHARS :]
    assert EMAIL not in base64.b64decode(head).decode("utf-8", "ignore")
    assert EMAIL.encode() not in base64.b64decode(tail + "==").rstrip()

    assert "BASE64_OBFUSCATED_PII" not in _entity_types(f"attachment {blob} end")


def test_an_oversized_blob_with_no_pii_is_not_flagged_as_pii():
    """Control. A plain attachment must not become a PII hit."""
    blob = _oversized(b"B" * 9000)

    assert "BASE64_OBFUSCATED_PII" not in _entity_types(f"attachment {blob} end")


@pytest.mark.parametrize("trim", [0, 1, 2, 3])
def test_tail_alignment_holds_whatever_the_blob_length(trim: int):
    """The tail guard must decode for every length remainder, not just the padded case."""
    payload = FILLER + b" " + EMAIL.encode() + b"x" * trim
    blob = _oversized(payload).rstrip("=")

    assert "BASE64_OBFUSCATED_PII" in _entity_types(f"attachment {blob} end")
