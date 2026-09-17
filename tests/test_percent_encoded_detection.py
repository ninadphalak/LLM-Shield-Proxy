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
