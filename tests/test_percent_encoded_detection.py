"""Percent-encoded PII must be detected, not forwarded.

`bob%40example.com` matches no email pattern and decodes back to an address in the
client. The v2 conformance profile measured this as a 0.40 leak rate on percent-encoded
cases against 0.09 on plain ones: the benchmark's inspector decoded before matching and
the engine did not, and the asymmetry was the defect.

These tests pin the detection, the absence of a false positive on ordinary
percent-encoded URLs, and the linear-time scan. The last one matters because
`detect_spans` runs per SSE event on the streaming hot path, where a pattern of the
shape `[^\\s]*%[0-9A-Fa-f]{2}[^\\s]*` would backtrack quadratically.
"""

from __future__ import annotations

import time

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine


def _types(text: str) -> set[str]:
    return {span[2] for span in pii_engine.detect_spans(text)}


@pytest.mark.parametrize(
    "text",
    [
        "contact bob%40example.com today",
        "contact%20bob%40example%2Ecom%20today",
        "ssn 456%2D78%2D9012 on file",
    ],
)
def test_percent_encoded_pii_is_detected(text: str) -> None:
    assert "PERCENT_OBFUSCATED_PII" in _types(text)


def test_plain_pii_still_detected_by_its_own_tier() -> None:
    # The percent pass must not shadow Tier 1: an unencoded address is still EMAIL,
    # not PERCENT_OBFUSCATED_PII.
    assert "EMAIL" in _types("contact bob@example.com today")


@pytest.mark.parametrize(
    "text",
    [
        "see https://example.com/a%20b for the docs",
        "the value is 100%25 complete",
        "nothing to see here at all",
        "a bare % sign and a %zz non-escape",
    ],
)
def test_percent_encoding_without_pii_is_not_flagged(text: str) -> None:
    # Redacting every percent-encoded URL would make the response path unusable.
    assert "PERCENT_OBFUSCATED_PII" not in _types(text)


def test_span_covers_the_whole_encoded_run() -> None:
    text = "contact bob%40example.com today"
    spans = [s for s in pii_engine.detect_spans(text) if s[2] == "PERCENT_OBFUSCATED_PII"]
    assert spans, "expected a percent span"
    start, end, _, matched = spans[0]
    # The source run is redacted rather than the decoded substring: decoding changes
    # offsets, and mapping them back is the fragile part.
    assert text[start:end] == matched == "bob%40example.com"


def test_scan_is_linear_on_a_run_with_no_valid_escape() -> None:
    # The backtracking trap: one long unbroken run ending in a non-escape.
    evil = "A" * 20000 + "%zz"
    start = time.perf_counter()
    pii_engine.detect_spans(evil)
    elapsed = time.perf_counter() - start
    # Quadratic backtracking on 20k characters takes seconds, not milliseconds.
    assert elapsed < 1.0, f"percent scan took {elapsed:.2f}s, suspect backtracking"


def test_oversized_run_is_skipped_not_decoded() -> None:
    from llm_shield_proxy.engines.pii_engine import MAX_PERCENT_INSPECTION_CHARS

    # Bounded inspection is an invariant: an attachment-sized blob must not be decoded.
    oversized = "x%41" * (MAX_PERCENT_INSPECTION_CHARS // 2)
    assert len(oversized) > MAX_PERCENT_INSPECTION_CHARS
    start = time.perf_counter()
    pii_engine.detect_spans("bob%40example.com " + oversized)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0


def test_oversized_run_still_scans_its_edges() -> None:
    """Greptile P1 on PR #38: an oversized run was skipped, so the bound was a recipe.

    Padding a percent-encoded value past MAX_PERCENT_INSPECTION_CHARS used to disable
    the decoder for that run entirely. The edges are now decoded, matching what
    BASE64_BOUNDARY_SCAN_CHARS already did for attachment-sized base64 bodies.

    The trailing fixture ends `%20bob%40example.com`, i.e. the address is preceded by an
    encoded space, and that separator is load-bearing rather than decoration.

    PR #39 bounded the EMAIL local part to `{1,64}` to kill its quadratic backtracking.
    Welding an address onto the end of an unbroken 8,192-character run therefore makes
    something that is not an address: the local part would be the whole run. The engine
    treats that identically with no percent-encoding anywhere in sight --
    `("a" * 70) + "bob@example.com"` yields no EMAIL span either -- so asserting on the
    glued form would be asserting that the edge scan does what the detector deliberately
    refuses to do. The control below pins exactly that, so this stays a decision rather
    than drifting back.

    The original fixture predates #39 and passed only against the unbounded pattern.
    """
    from llm_shield_proxy.engines.pii_engine import MAX_PERCENT_INSPECTION_CHARS

    padding = "x" * MAX_PERCENT_INSPECTION_CHARS
    leading = "bob%40example.com" + padding
    trailing = padding + "%20bob%40example.com"
    assert len(leading) > MAX_PERCENT_INSPECTION_CHARS
    assert len(trailing) > MAX_PERCENT_INSPECTION_CHARS
    assert "PERCENT_OBFUSCATED_PII" in _types(leading), "value at the head was missed"
    assert "PERCENT_OBFUSCATED_PII" in _types(trailing), "value at the tail was missed"


def test_a_value_glued_to_a_long_unbroken_run_is_not_an_address() -> None:
    """Why the test above needs a separator, pinned independently of percent-encoding.

    #39 bounds the EMAIL local part at 64 characters. An address with 70 junk characters
    fused to the front of it is not one, and no client would parse it as one either.
    """
    assert "EMAIL" not in _types(("a" * 70) + "bob@example.com")
    assert "EMAIL" in _types(("a" * 70) + " bob@example.com")


def test_oversized_run_decodes_only_its_two_edges(monkeypatch) -> None:
    """Bounded work, asserted structurally rather than by the clock.

    A wall-clock assertion here measured the wrong thing: `detect_spans` has a
    pre-existing quadratic in the EMAIL pattern on long `[A-Za-z0-9._%+-]` runs, which
    dominates any timing and has nothing to do with this code path. What this block owes
    is that an arbitrarily large run costs at most two bounded decodes.
    """
    from llm_shield_proxy.engines import pii_engine as module

    decoded: list[int] = []
    real_unquote = module.unquote

    def counting_unquote(value, *args, **kwargs):
        decoded.append(len(value))
        return real_unquote(value, *args, **kwargs)

    monkeypatch.setattr(module, "unquote", counting_unquote)
    oversized = "bob%40example.com" + ("%41" * module.MAX_PERCENT_INSPECTION_CHARS)
    module.pii_engine.detect_spans(oversized)

    assert decoded, "the oversized run was skipped entirely, which is the bypass"
    assert len(decoded) <= 2, f"decoded {len(decoded)} times, expected at most two edges"
    assert max(decoded) <= module.PERCENT_BOUNDARY_SCAN_CHARS
