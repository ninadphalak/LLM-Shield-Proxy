"""Measure the benign floor behind `partial_emission.MIN_SPECIFIC_RUN`.

The external-gateway partial-emission detector flags a response when the complete
injected value is absent but a long enough run of it arrived. "Long enough" has to sit
above every run a CORRECT gateway produces by coincidence, and those coincidences are
real in this corpus: the echo and injection emails share a domain, several published
test PANs share long runs of `1`, a gateway that replaces a value with a fixture-shaped
substitute emits text shaped like the needle, and the chunk envelope carries a ten-digit
timestamp.

For every seed and every covering-array case this builds the SSE a correct gateway
returns -- the echoed prompt (rehydrated), the capture's own carrier text, the injected
value REPLACED in one of seven styles, and an OpenAI-style chunk envelope (`id`,
`object`, `created`, `model`, `system_fingerprint`) on every event -- and reports three
numbers per style and entity:

    raw         longest run of the needle in the published `same-path-join` haystacks
    all-fields  `specific_run` over those same haystacks (client-supplied text excluded)
    detector    `specific_run` over `response_haystacks` (envelope excluded) -- the
                number the detector compares against MIN_SPECIFIC_RUN

`--http-seeds N` additionally drives the real in-process gateway over loopback with the
correct reference policies at every two-part split, checking the simulation against the
actual code path.

    python benchmarks/partial_emission_threshold.py --seeds 20000 --http-seeds 20
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pii-leak-benchmark"))

from pii_leak_benchmark.http_profile import _needle_proximity, _normalize  # noqa: E402
from pii_leak_benchmark.partial_emission import (  # noqa: E402
    MIN_SPECIFIC_RUN,
    benign_texts,
    response_haystacks,
    score_case,
    specific_run,
)
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    _haystack_groups,
    build_segments,
    covering_array,
    make_seeded_fixture,
)

PRESIDIO_TAG = {
    "EMAIL": "<EMAIL_ADDRESS>",
    "SSN": "<US_SSN>",
    "CARDPAN": "<CREDIT_CARD>",
    "USPHONE": "<PHONE_NUMBER>",
}
STYLES = (
    "redacted",
    "presidio-tag",
    "placeholder",
    "stars",
    "dropped",
    "gateway-note",
    "synthetic-substitute",
)
METRICS = ("raw", "all-fields", "detector")


def _sse(deltas: list[dict], rng: random.Random) -> str:
    alnum = string.ascii_letters + string.digits
    envelope = {
        "id": "chatcmpl-" + "".join(rng.choice(alnum) for _ in range(29)),
        "object": "chat.completion.chunk",
        "created": rng.randint(1_700_000_000, 1_900_000_000),
        "model": "gpt-4o-mini-2024-07-18",
        "system_fingerprint": "fp_" + "".join(rng.choice("0123456789abcdef") for _ in range(10)),
    }
    frames = [
        "data: " + json.dumps({**envelope, "choices": [{"index": 0, "delta": d, "finish_reason": None}]}) + "\n\n"
        for d in deltas
    ]
    return "".join(frames) + "data: [DONE]\n\n"


def _responses(segments, case, rng: random.Random):
    """(style, sse) for each replacement style a correct gateway might use."""
    entity = case["entity"]
    needle = segments.injection[entity]
    prompt = "Please review: " + ", ".join(segments.echo.values())
    substitute = needle
    while _normalize(substitute) == _normalize(needle):
        # A substitute equal to the needle IS the needle: that is a leak, not a collision.
        substitute = make_seeded_fixture(rng)[entity]
    replacements = {
        "redacted": "[REDACTED]",
        "presidio-tag": PRESIDIO_TAG[entity],
        "placeholder": f"[{entity}_1]",
        "stars": "****",
        "dropped": "",
        "gateway-note": "[removed: this value matched a sensitive-data policy and was withheld]",
        "synthetic-substitute": substitute,
    }
    for style, replacement in replacements.items():
        deltas = [{"content": "You sent: " + prompt + "\n"}]
        if case["carrier"] == "sse-delta-content":
            deltas += [{"content": "Reference record: "}, {"content": replacement}]
        else:
            deltas += [{"content": "Reference record attached."}, {"content": "", "record_field": replacement}]
        yield style, _sse(deltas, rng)


def _simulate_chunk(args: tuple[int, int]) -> dict:
    start, count = args
    rng = random.Random(start)
    cases = covering_array()
    top: dict[str, int] = defaultdict(int)
    low: dict[str, int] = defaultdict(lambda: 10**9)
    histogram: dict[str, int] = defaultdict(int)
    lengths: dict[str, int] = {}
    total = 0
    for _ in range(count):
        segments = build_segments(format(rng.getrandbits(64), "016x"))
        for case in cases:
            entity = case["entity"]
            needle = segments.injection[entity]
            lengths[entity] = len(_normalize(needle))
            benign = benign_texts(segments, case)
            for style, sse in _responses(segments, case, rng):
                total += 1
                published = _haystack_groups(sse)["same-path-join"]
                values = {
                    "raw": _needle_proximity(_normalize(needle), tuple(_normalize(h) for h in published)),
                    "all-fields": specific_run(needle, published, benign),
                    "detector": specific_run(needle, response_haystacks(sse), benign),
                }
                for metric, value in values.items():
                    key = f"{style}|{entity}|{metric}"
                    top[key] = max(top[key], value)
                    low[key] = min(low[key], value)
                histogram[f"{style}|{values['detector']}"] += 1
    return {"top": dict(top), "low": dict(low), "histogram": dict(histogram), "lengths": lengths, "total": total}


def simulate(seeds: int, workers: int) -> None:
    per = max(1, seeds // (workers * 4))
    chunks = [(start, min(per, seeds - start)) for start in range(0, seeds, per)]
    top: dict[str, int] = defaultdict(int)
    low: dict[str, int] = defaultdict(lambda: 10**9)
    histogram: dict[str, int] = defaultdict(int)
    lengths: dict[str, int] = {}
    total = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(_simulate_chunk, chunks):
            for k, v in part["top"].items():
                top[k] = max(top[k], v)
            for k, v in part["low"].items():
                low[k] = min(low[k], v)
            for k, v in part["histogram"].items():
                histogram[k] += v
            lengths.update(part["lengths"])
            total += part["total"]
    cases = len(covering_array())
    print(f"simulated responses: {total} ({seeds} seeds x {cases} cases x {len(STYLES)} styles)")
    print("normalised needle lengths:", dict(sorted(lengths.items())))
    print(f"{'style':22} {'entity':8} {'raw':>9} {'all-fields max':>15} {'detector max':>13}")
    for style in STYLES:
        for entity in sorted(lengths):
            raw = f"{low[f'{style}|{entity}|raw']}-{top[f'{style}|{entity}|raw']}"
            print(
                f"{style:22} {entity:8} {raw:>9} {top[f'{style}|{entity}|all-fields']:>15} "
                f"{top[f'{style}|{entity}|detector']:>13}"
            )
    print("detector specific_run histogram per style:")
    for style in STYLES:
        row = {int(k.split("|")[1]): v for k, v in histogram.items() if k.startswith(style + "|")}
        print(f"  {style:22}", dict(sorted(row.items())))
    for metric in METRICS[1:]:
        print(f"max {metric} specific_run over every style: {max(v for k, v in top.items() if k.endswith(metric))}")
    print(f"MIN_SPECIFIC_RUN currently: {MIN_SPECIFIC_RUN}")


def over_http(seeds: int, policies: tuple[str, ...]) -> None:
    rng = random.Random(1)
    worst: dict[tuple[str, str], int] = defaultdict(int)
    tally: dict[str, dict[str, int]] = {p: defaultdict(int) for p in policies}
    for _ in range(seeds):
        segments = build_segments(format(rng.getrandbits(64), "016x"))
        for case in covering_array():
            for policy in policies:
                oracle = "exhaustive-2-part" if case["fragmentation"] == "adversarial" else "midpoint"
                row = score_case(segments, policy, case, oracle=oracle)
                if row.transport_error:
                    raise RuntimeError(row.transport_error)
                key = (policy, case["entity"])
                worst[key] = max(worst[key], row.longest_specific_run)
                counts = tally[policy]
                counts["partitions"] += row.partitions_tried
                counts["partial (exact oracle)"] += row.partitions_partial
                counts["flagged by threshold"] += row.threshold_partitions
                counts["invariance violations"] += row.invariance_violations or 0
                counts["flagged by threshold, cleared by exact oracle"] += row.threshold_only_partitions or 0
                counts["complete-value leaks"] += row.complete_leak_partitions
    print(f"over HTTP: {seeds} seeds, every two-part split of every adversarial case")
    for policy in policies:
        print(f"  {policy}: " + ", ".join(f"{k} {v}" for k, v in tally[policy].items()))
    for key, value in sorted(worst.items()):
        print(f"  {key[0]:24} {key[1]:8} longest specific_run {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=2000)
    parser.add_argument("--http-seeds", type=int, default=0)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()
    simulate(args.seeds, args.workers)
    if args.http_seeds:
        over_http(args.http_seeds, ("bounded-retention", "retention-plus-decoding", "redact-all", "chunk-local"))


if __name__ == "__main__":
    main()
