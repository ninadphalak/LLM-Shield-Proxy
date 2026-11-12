"""Stream-shape partitions: a separately-versioned response-path check.

THE GAP. The v2 profile cuts an injected value once (the midpoint), or at every single
offset (`exhaustive-2-part`), or at every pair of offsets (`exhaustive-3-part`). Real
providers do not deliver a value that way:

* many send about one tokenizer token per event, so a value arrives as many small pieces,
  digit groups and punctuation apart (`user`, `@example`, `.`, `com`);
* a piece often ends right after a `.`, `?` or `!` inside the value (`user@example.`);
* other providers batch a few tokens per event, so one event can end inside a value and the
  next can start a new sentence;
* a stream has many boundaries, so a gateway that is right whenever a value spans at most
  two or three events can still fail when it spans six.

WHY A SEPARATE INSTRUMENT. Same reason as `partial_emission`: every published v2 row carries
`instrument.inspector_sha256`, a digest of the scorer functions, and the partition oracle is
inside that digest. Adding families to it would change what every published row means. So
this module runs its own pass over the same corpus, the same capture and the same request,
cutting the value into the families below, and writes its own artefact under its own schema
id (`STREAM_SHAPES_SCHEMA_ID`) with its own source digest. The v2 report, its rates and its
digest are untouched.

FAMILIES (cuts are offsets inside the rendered value; every piece is its own SSE event):

* `token-aligned`: the value as a token stream, one token per event and in batches of 2, 3
  and 4 tokens. Tokens come from a small word, digit-group and punctuation tokenizer in this
  module, so the harness keeps its no-dependency rule.
* `punctuation-cut`: for each `.`, `?` or `!` inside the value, the value cut once right
  after it, and the token stream with an extra cut right after it.
* `per-character`: one character per event.
* `n-part`: seeded random partitions into exactly 3, 4, 6 and 8 pieces.
* `stream-union`: all of the above; a case leaks if any partition leaks.

SCORING. A partition leaks when the complete value can be recovered from what the client
received (`v2_emitter._leak_tier`, the v2 profile's own test). Partial emission is reported
beside it with the `partial_emission` threshold, never folded into the leak count.

NOT COVERED YET: two or more detectors firing on one stream (for example a card and an API
key in the same answer). The v2 corpus puts one entity in each case, and the injection
events are inside the published digest, so a two-entity case belongs in the next instrument
revision rather than here.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from typing import Any, Iterable

import httpx

from .partial_emission import MIN_SPECIFIC_RUN, benign_texts, response_haystacks, specific_run
from .v2_emitter import (
    POLICIES,
    UpstreamState,
    _behaviour_source,
    _client_timeout,
    _encode,
    _extra_gateway_headers,
    _leak_tier,
    _make_gateway,
    _make_upstream,
    _self_probe,
    _serve,
    _stop,
    build_request,
    build_segments,
    covering_array,
)

STREAM_SHAPES_SCHEMA_ID = "llm-shield.stream-shapes/v0.1.0-draft"
FAMILIES: tuple[str, ...] = ("token-aligned", "punctuation-cut", "per-character", "n-part")
ALL_FAMILIES: tuple[str, ...] = FAMILIES + ("stream-union",)
MARKS = ".?!"

# Letters and symbol runs take one leading space; digit runs of at most three take none
# (byte-pair tokenizers group digits that way); then bare whitespace.
_TOKEN = re.compile(r" ?[^\W\d_]+|\d{1,3}| ?[^\w\s]+| ?_+|\s+(?!\S)|\s")


def simple_tokens(text: str) -> list[str]:
    """Words, digit groups of up to three and punctuation runs; joins back to `text`."""
    tokens = _TOKEN.findall(text)
    if "".join(tokens) != text:  # pragma: no cover - the pattern covers every character
        raise RuntimeError("tokenizer lost characters")
    return tokens


def _boundaries(tokens: list[str]) -> list[int]:
    cuts, offset = [], 0
    for token in tokens[:-1]:
        offset += len(token)
        cuts.append(offset)
    return cuts


def family_partitions(value: str, family: str) -> list[tuple[int, ...]]:
    """Every partition of `value` in one family, as sorted tuples of internal cut offsets.

    Deterministic for a given value. Partitions with a single piece are dropped: the v2
    single-chunk arm is the control, and it is measured there.
    """
    n = len(value)
    out: list[tuple[int, ...]] = []
    if family == "token-aligned":
        tokens = simple_tokens(value)
        for size in (1, 2, 3, 4):
            groups = ["".join(tokens[i : i + size]) for i in range(0, len(tokens), size)]
            out.append(tuple(_boundaries(groups)))
    elif family == "punctuation-cut":
        token_cuts = set(_boundaries(simple_tokens(value)))
        for i in range(n - 1):
            if value[i] in MARKS and not value[i + 1].isspace():
                out.append((i + 1,))
                out.append(tuple(sorted(token_cuts | {i + 1})))
    elif family == "per-character":
        out.append(tuple(range(1, n)))
    elif family == "n-part":
        rng = random.Random(f"stream-shapes:{value}")
        for parts in (3, 4, 6, 8):
            if parts - 1 > n - 1:
                continue
            for _ in range(8):
                out.append(tuple(sorted(rng.sample(range(1, n), parts - 1))))
    elif family == "stream-union":
        for name in FAMILIES:
            out.extend(family_partitions(value, name))
    else:
        raise ValueError(f"unknown stream-shape family {family!r}; known: {ALL_FAMILIES}")
    seen: set[tuple[int, ...]] = set()
    unique = []
    for cuts in out:
        if cuts and cuts not in seen:
            seen.add(cuts)
            unique.append(cuts)
    return unique


def probe_case(
    segments: Any,
    policy_name: str,
    case: dict[str, str],
    family: str,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> dict[str, Any]:
    """Drive one adversarial case through every partition of `family` and score it."""
    if gateway_url is None and policy_name not in POLICIES:
        raise ValueError(f"{policy_name!r} is not an in-process policy; pass gateway_url")
    needle = segments.injection[case["entity"]]
    rendered = _encode(needle, case["encoding"])
    partitions = family_partitions(rendered, family)
    benign = benign_texts(segments, case, model=model)
    row: dict[str, Any] = {
        "case": dict(case),
        "family": family,
        "partitions_tried": 0,
        "complete_leak_partitions": 0,
        "partial_emission_partitions": 0,
        "example_leaking_pieces": None,
        "transport_error": None,
    }
    state = UpstreamState(segments=segments, case=case)
    upstream, upstream_url = _serve(_make_upstream(state), port=upstream_port)
    gateway = None
    try:
        _self_probe(upstream_url, state)
        if gateway_url is None:
            gateway, gateway_url = _serve(_make_gateway(upstream_url, policy_name))
        body = json.dumps(build_request(segments, case, model=model)).encode()
        headers = {"Content-Type": "application/json", "Connection": "close"}
        token = os.environ.get("V2_GATEWAY_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers.update(_extra_gateway_headers())
        with httpx.Client(timeout=_client_timeout(), trust_env=False) as client:
            for cuts in partitions:
                state.cuts = cuts
                before = len(state.received_bodies)
                try:
                    response = client.post(gateway_url, content=body, headers=headers)
                    response.raise_for_status()
                    sse = response.content.decode("utf-8", "replace")
                except Exception as exc:  # noqa: BLE001
                    row["transport_error"] = f"{type(exc).__name__}: {exc}"
                    break
                if len(state.received_bodies) == before:
                    row["transport_error"] = "capture recorded no request; nothing about the gateway was measured"
                    break
                row["partitions_tried"] += 1
                complete = _leak_tier(needle, sse) is not None
                if complete:
                    row["complete_leak_partitions"] += 1
                    if row["example_leaking_pieces"] is None:
                        bounds = (0, *cuts, len(rendered))
                        row["example_leaking_pieces"] = [rendered[a:b] for a, b in zip(bounds, bounds[1:])]
                elif specific_run(needle, response_haystacks(sse), benign) >= MIN_SPECIFIC_RUN:
                    row["partial_emission_partitions"] += 1
    finally:
        _stop(upstream)
        if gateway is not None:
            _stop(gateway)
    row["leaked"] = row["transport_error"] is None and row["complete_leak_partitions"] > 0
    return row


def stream_shapes_digest() -> str:
    """Fingerprint of this check's own scoring source, so an artefact names what made it."""
    names = ("simple_tokens", "_boundaries", "family_partitions", "probe_case", "build_stream_shapes_report")
    source = "\n".join(_behaviour_source(globals()[name]) for name in names)
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def build_stream_shapes_report(rows: list[dict[str, Any]], seed: str, label: str, family: str) -> dict[str, Any]:
    adversarial = [r for r in rows if r["transport_error"] is None]
    leaked = [r for r in adversarial if r["leaked"]]
    by_entity: dict[str, dict[str, int]] = {}
    for r in adversarial:
        slot = by_entity.setdefault(r["case"]["entity"], {"cases": 0, "leaked": 0})
        slot["cases"] += 1
        slot["leaked"] += int(r["leaked"])
    return {
        "schema": STREAM_SHAPES_SCHEMA_ID,
        "label": label,
        "seed": seed,
        "family": family,
        "instrument": {"stream_shapes_sha256": stream_shapes_digest()},
        "cases_scored": len(adversarial),
        "cases_inconclusive": len(rows) - len(adversarial),
        "cases_leaked": len(leaked),
        "leak_rate": round(len(leaked) / len(adversarial), 4) if adversarial else None,
        "partitions_tried": sum(r["partitions_tried"] for r in adversarial),
        "partitions_leaked": sum(r["complete_leak_partitions"] for r in adversarial),
        "partial_emission_partitions": sum(r["partial_emission_partitions"] for r in adversarial),
        "by_entity": by_entity,
        "cases": rows,
        "method_limit": (
            "Partitions of the injected value only, from a built-in tokenizer and seeded random "
            "cuts; not every stream a provider can send, and not a two-detector case."
        ),
    }


def run_stream_shapes(
    policy_name: str,
    family: str = "stream-union",
    seed: str | None = None,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> dict[str, Any]:
    """Score one in-process policy or one external gateway over the v2 corpus's adversarial
    cases. Pass a v2 run's seed to describe the same fixture as that run's report."""
    import secrets

    if family not in ALL_FAMILIES:
        raise ValueError(f"unknown stream-shape family {family!r}; known: {ALL_FAMILIES}")
    seed = seed or secrets.token_hex(8)
    segments = build_segments(seed)
    rows = [
        probe_case(
            segments, policy_name, case, family, gateway_url=gateway_url, upstream_port=upstream_port, model=model
        )
        for case in covering_array()
        if case["fragmentation"] == "adversarial"
    ]
    return build_stream_shapes_report(rows, seed, policy_name, family)


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="pii-leak-benchmark-stream-shapes",
        description="Send each injected value as token-aligned, punctuation-cut, per-character and "
        "many-part streams, and report which ones reach the client whole. Separate from the v2 report.",
    )
    parser.add_argument(
        "--only", default="chunk-local,bounded-retention", help="policies, or a label with --gateway-url"
    )
    parser.add_argument("--family", default="stream-union", choices=ALL_FAMILIES)
    parser.add_argument("--seed", default=None)
    parser.add_argument("--gateway-url", default=None)
    parser.add_argument("--upstream-port", type=int, default=0)
    parser.add_argument("--model", default="test")
    parser.add_argument("--json-out", default=None, help="write all reports to this file")
    args = parser.parse_args(list(argv) if argv is not None else None)
    names = [n.strip() for n in args.only.split(",") if n.strip()]
    reports = []
    for name in names:
        report = run_stream_shapes(
            name,
            args.family,
            args.seed,
            gateway_url=args.gateway_url,
            upstream_port=args.upstream_port,
            model=args.model,
        )
        reports.append(report)
        print(
            f"{name:28} family={args.family:16} cases={report['cases_scored']:3} leaked={report['cases_leaked']:3} "
            f"leak_rate={report['leak_rate']} partitions={report['partitions_tried']} "
            f"partitions_leaked={report['partitions_leaked']} partial={report['partial_emission_partitions']}"
        )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(reports, handle, indent=1)
    return 1 if any(r["cases_leaked"] or r["cases_inconclusive"] for r in reports) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
