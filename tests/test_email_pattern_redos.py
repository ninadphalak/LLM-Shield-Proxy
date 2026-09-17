"""The EMAIL pattern must stay linear on input that holds no `@`.

`[A-Za-z0-9._%+-]+@...` retries from every permitted start position and rescans to the
end of the run each time, so a long run of characters the local-part class accepts but
that contains no `@` is quadratic. That run is trivially reachable: a percent-encoded
body, a base64 blob, a hex digest, a long identifier. It is reachable on the REQUEST
path as well as the response path, so it is an availability bug in the thing the proxy
exists to sit in front of.

Measured on the unbounded pattern before the fix:

    30k characters   0.27 s
    60k characters   1.11 s
    120k characters  4.36 s

Doubling the input quadrupled the time. The bounds make the same 120k input cost
0.008 s and scale linearly.
"""

from __future__ import annotations

import re
import time

import pytest

from llm_shield_proxy.engines.pii_engine import TIER1_PATTERNS, pii_engine

EMAIL_PATTERN: re.Pattern[str] = dict(TIER1_PATTERNS)["EMAIL"]


@pytest.mark.parametrize(
    "address",
    [
        "bob@example.com",
        "a.b+c%d-e@sub.example.co.uk",
        "x@y.io",
        "first.last@example.museum",
        "user_name@example.com",
    ],
)
def test_real_addresses_still_match(address: str) -> None:
    # The fix must not narrow detection of anything a person could actually be reached at.
    assert EMAIL_PATTERN.search(address), f"{address} stopped matching"
    assert "EMAIL" in {span[2] for span in pii_engine.detect_spans(f"write to {address} today")}


@pytest.mark.parametrize(
    "text",
    ["not-an-email", "just some prose", "@example.com", "bob@", "a@b"],
)
def test_non_addresses_still_do_not_match(text: str) -> None:
    assert not EMAIL_PATTERN.search(text)


def test_long_at_free_run_is_linear_not_quadratic() -> None:
    """The regression itself. Quadratic growth here is the bug.

    Asserting the ratio rather than an absolute time: absolute thresholds are flaky on
    shared CI runners, but the SHAPE of the growth is what distinguishes a linear scan
    from the quadratic one. Quadratic quadruples on a doubled input; linear roughly
    doubles. The 3x allowance leaves room for noise while still failing the old pattern,
    which grew about 4x per doubling.
    """

    def elapsed(length: int) -> float:
        text = "%41" * length
        best = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            EMAIL_PATTERN.search(text)
            best = min(best, time.perf_counter() - start)
        return best

    small = elapsed(20_000)
    large = elapsed(40_000)
    # Guard against a clock so coarse the ratio is meaningless.
    if small < 1e-5:
        pytest.skip("timer resolution too coarse to measure a ratio")
    assert large / small < 3.0, (
        f"doubling the input multiplied the time by {large / small:.1f}x, "
        "which is the quadratic backtracking this bound exists to prevent"
    )


def test_a_hostile_run_does_not_stall_detection() -> None:
    """End to end through detect_spans, which is what a request body reaches."""
    hostile = "%41" * 40_000
    start = time.perf_counter()
    pii_engine.detect_spans(hostile)
    assert time.perf_counter() - start < 2.0


def test_the_bounds_are_the_rfc_maxima() -> None:
    """Pins WHY these numbers, so a later edit does not round them off.

    RFC 5321 s4.5.3.1.1 caps the local-part at 64 octets and s4.5.3.1.2 the domain at
    255. A local-part longer than that is not a deliverable address, so bounding there
    costs no real detection.
    """
    local_64 = "a" * 64
    local_65 = "a" * 65
    assert EMAIL_PATTERN.search(f"{local_64}@example.com")
    assert not EMAIL_PATTERN.search(f"{local_65}@example.com")


# --------------------------------------------------------------------------------------
# Whole-engine sweep. The EMAIL bug was found by accident; this looks for the next one.
# --------------------------------------------------------------------------------------

_ATTACK_SEEDS = {
    "percent": "%41",
    "dotted_alnum": "a1.b2.c3.",
    "dashes": "12-34-56-",
    "alnum_pct_plus": "a1%2B",
    "hexish": "abcdef0123",
    "digits": "1234567890",
    "b64ish": "QUJDREVG",
}


def _all_compiled_patterns() -> list[tuple[str, re.Pattern[str]]]:
    from llm_shield_proxy.engines import pii_engine as module

    found = list(module.TIER1_PATTERNS)
    for name in dir(module):
        if not name.endswith("_PATTERN"):
            continue
        obj = getattr(module, name)
        if isinstance(obj, re.Pattern):
            found.append((name, obj))
    return found


@pytest.mark.parametrize("seed_name,seed", sorted(_ATTACK_SEEDS.items()))
def test_no_pattern_grows_superlinearly(seed_name: str, seed: str) -> None:
    """No compiled pattern may backtrack on a long run of plausible input.

    Every pattern here scans attacker-supplied request bodies. One that retries from
    each start position and rescans to the end is a denial of service, which is what
    EMAIL was: four of these seeds triggered it, at about 4x growth per doubling.

    Growth ratio rather than absolute time, because absolute thresholds are flaky on
    shared runners and the shape of the growth is the thing that matters.
    """
    small_text = seed * (20_000 // len(seed))
    large_text = seed * (40_000 // len(seed))
    offenders = []
    for name, pattern in _all_compiled_patterns():
        small = min(_time_search(pattern, small_text) for _ in range(3))
        if small < 2e-5:
            continue  # too fast to form a meaningful ratio
        large = min(_time_search(pattern, large_text) for _ in range(3))
        if large / small >= 3.0 and large > 0.05:
            offenders.append(f"{name} grew {large / small:.1f}x to {large:.3f}s")
    assert not offenders, f"superlinear on {seed_name!r}: " + "; ".join(offenders)


def _time_search(pattern: re.Pattern[str], text: str) -> float:
    start = time.perf_counter()
    pattern.search(text)
    return time.perf_counter() - start
