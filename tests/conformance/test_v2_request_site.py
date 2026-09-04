"""The request_site axis, and the property that justifies its existence.

Until 2026-09-04 every case in the v2 profile put its protected values in
`messages[0].content`. Nothing in an emitted report said so, so a gateway that walked only
the chat shapes it knows by name scored exactly the same as one that walked the whole
request body -- and a real MCP or JSON-RPC caller puts values in a system message, in tool
arguments, and in keys no schema names.

The tests that matter here are the last two: a chat-shapes-only gateway must score WORSE
than a whole-body one. If it does not, the axis is decoration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from collections import Counter  # noqa: E402

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    AXES,
    _all_pairs,
    _pairs_of,
    _STRUCTURAL_KEYS,
    build_request,
    build_segments,
    covering_array,
    extract_site,
)

SEED = "a1b2c3d4e5f60001"


def _case(site: str) -> dict[str, str]:
    return {
        "entity": "EMAIL",
        "encoding": "plain",
        "fragmentation": "single_chunk",
        "carrier": "sse-delta-content",
        "request_site": site,
    }


def test_request_site_is_a_scored_axis() -> None:
    assert "request_site" in AXES
    assert len(AXES["request_site"]) >= 2, "one site is a constant, not an axis"


def test_every_site_round_trips() -> None:
    """What `build_request` places, `extract_site` must find. Otherwise the capture
    echoes nothing and every case on that site reports an unmeasurable echo."""
    segments = build_segments(SEED)
    for site in AXES["request_site"]:
        body = build_request(segments, _case(site))
        found = extract_site(body, site)
        assert found is not None, f"{site}: placed nothing extract_site can read back"
        for value in segments.echo.values():
            assert value in found, f"{site}: {value} not carried at its own site"


def test_sites_are_actually_distinct_locations() -> None:
    """Two sites that serialise to the same body would inflate the covering array
    without testing anything."""
    segments = build_segments(SEED)
    bodies = {
        site: json.dumps(build_request(segments, _case(site)), sort_keys=True)
        for site in AXES["request_site"]
    }
    assert len(set(bodies.values())) == len(bodies), "two request sites produce one body"


def test_no_site_hides_values_in_a_structural_field() -> None:
    """Masking `model`, `role`, `type` or a function `name` changes what a request MEANS.
    A profile that placed protected values there would be scoring the wrong behaviour."""
    segments = build_segments(SEED)
    needles = list(segments.echo.values())

    def walk(node: object, key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in _STRUCTURAL_KEYS:
            for needle in needles:
                assert needle not in node, f"protected value placed in structural key {key!r}"

    for site in AXES["request_site"]:
        walk(build_request(segments, _case(site)))


def test_covering_array_reaches_every_site() -> None:
    cases = covering_array()
    seen = {c["request_site"] for c in cases}
    assert seen == set(AXES["request_site"]), f"sites never exercised: {set(AXES['request_site']) - seen}"


def test_a_chat_only_gateway_is_caught() -> None:
    """The property the axis exists for.

    Model two request-path gateways over the same corpus: one that masks the whole body,
    one that masks only `messages[*].content`. Under the old fixed-site corpus they were
    indistinguishable. They must not be now.
    """
    segments = build_segments(SEED)
    needles = list(segments.echo.values())

    def mask_whole_body(node: object, key: str | None = None) -> object:
        if isinstance(node, dict):
            return {k: mask_whole_body(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [mask_whole_body(v, key) for v in node]
        if isinstance(node, str) and key not in _STRUCTURAL_KEYS:
            for needle in needles:
                node = node.replace(needle, "[REDACTED]")
        return node

    def mask_chat_only(body: dict) -> dict:
        out = json.loads(json.dumps(body))
        for message in out.get("messages", []):
            for needle in needles:
                message["content"] = message["content"].replace(needle, "[REDACTED]")
        return out

    def egressed(masker, site: str) -> bool:
        body = masker(build_request(segments, _case(site)))
        blob = json.dumps(body)
        return any(needle in blob for needle in needles)

    sites = list(AXES["request_site"])
    whole = [s for s in sites if egressed(mask_whole_body, s)]
    chat_only = [s for s in sites if egressed(mask_chat_only, s)]

    assert whole == [], f"whole-body masker leaked at {whole}"
    assert chat_only, (
        "a chat-shapes-only gateway leaked at no site, so the request_site axis "
        "distinguishes nothing and should not be in the schema"
    )
    # And the specific shapes a chat-only walk misses, named so a regression says which.
    assert "unrecognised-key" in chat_only
    assert "tool-description" in chat_only


def test_chat_content_site_still_behaves_like_the_old_corpus() -> None:
    """Regression guard: the original behaviour must survive as one case among several,
    or the five-axis numbers are not comparable to the four-axis ones at all."""
    segments = build_segments(SEED)
    body = build_request(segments, _case("chat-content"))
    content = body["messages"][0]["content"]
    assert content.startswith("Please review: ")
    for value in segments.echo.values():
        assert value in content


@pytest.mark.parametrize("site", ["not-a-site", "", "messages"])
def test_unknown_site_raises(site: str) -> None:
    """A typo in a case definition must fail loudly. Silently placing values nowhere
    would score the gateway as perfectly safe."""
    segments = build_segments(SEED)
    with pytest.raises(ValueError):
        build_request(segments, _case(site))


def test_fragmentation_is_paired_within_case() -> None:
    """DeltaFrag is a difference of two leak rates, so the two fragmentation conditions
    must be otherwise identical populations.

    A greedy pairwise array does not give that on its own: the first five-axis array came
    out 8 single_chunk against 4 adversarial, and DeltaFrag was then comparing two
    differently-composed sets while attributing the difference to fragmentation. The
    observable consequence was a **negative** DeltaFrag from a real cloud detector.

    Every case must therefore have a twin differing only in `fragmentation`.
    """
    cases = covering_array()
    keys = {tuple(sorted(c.items())) for c in cases}
    for case in cases:
        for value in AXES["fragmentation"]:
            twin = tuple(sorted(dict(case, fragmentation=value).items()))
            assert twin in keys, f"{case} has no {value} counterpart"

    counts = Counter(c["fragmentation"] for c in cases)
    assert len(set(counts.values())) == 1, f"fragmentation conditions unbalanced: {counts}"


def test_pairing_did_not_break_the_coverage_proof() -> None:
    """Adding twins must not be an excuse for losing pairwise completeness."""
    cases = covering_array()
    covered: set = set()
    for case in cases:
        covered |= _pairs_of(case)
    required = _all_pairs()
    assert required <= covered, f"pairs lost: {sorted(required - covered)[:5]}"
