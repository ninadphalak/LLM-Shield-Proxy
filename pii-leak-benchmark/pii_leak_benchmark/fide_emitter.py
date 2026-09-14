"""FIDE profile emitter (draft spec v2.1.0): the response split, made class-agnostic.

WHAT THIS ADDS TO v2. One axis, `needle_class`, and the machinery to keep it honest.
Fragmentation-Induced Detection Evasion is a property of the TRANSPORT, not of personal
data: a boundary placed inside a value that the client reassembles is invisible to a
per-event detector whatever the value means. v2 measures that for PII only. This profile
measures it for PII and secrets in ONE run, against ONE detector configuration, so the
cross-class comparison is a within-run contrast rather than two directories read side by
side.

WHAT IT DELIBERATELY DOES NOT DO.

  * It does not fork the shared scoring path. Functions including `run_case`,
    `_haystacks`, `_leak_tier`, `_parse_sse`, `build_report`, and
    `injection_partitions` are imported from `v2_emitter`, so FIDE and v2 reports carry
    the same enumerated-source `inspector_sha256`. That non-transitive fingerprint is a
    change-detection check, not the whole basis for comparison. The shared call path,
    full source tag, corpus metadata, report validity, completeness, and configuration
    provenance carry the remaining burden.
  * It does not touch the v2 corpus. `spec/v2.0.0` and
    `benchmarks/results/v2-response-split/` remain the PII baseline. A sixth axis moves
    every case definition, so a six-axis row sitting beside a five-axis one would be two
    corpus generations in one directory -- the failure this project has already had once.
  * It does not populate `prompt_injection`. The class is declared in the registry with
    no fixtures behind it. That is a scope statement, not an omission, and it must not be
    filled in by the same change that lands the secret family.

THE TARGETS HERE ARE STUDY-OWNED CONTROLS, NOT PRODUCTS. The reference policies below
carry a detector set that is the v2 PII patterns plus four secret patterns TRANSCRIBED
from detect-secrets 1.5.0 with the source cited per pattern. They are models chosen to
land in known quadrants. Nothing in this module names a vendor's streaming product or
attributes a result to one. A third-party secret scanner is measured the way Presidio and
LLM Guard are measured -- as a separately named, versioned, configured wrapper with its
own row -- and never by giving a PII wrapper a secret regex and keeping the old label.

THE RETENTION CONTROL IS TWO CONTROLS, and that is the most useful thing in this file.
v2's `bounded-retention` cuts its buffer at the last whitespace before the tail, which is
sound only because every v2 needle is a whitespace-free run. A PEM block is not. Both
rules are measured here, so the claim "retention removes the fragmentation increment"
gets its own boundary condition measured rather than assumed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

from . import needle_registry as nr
from .artifact import write_json_artifact
from .needle_registry import NEEDLES, class_of, measurable_ids, needle_values
from .v2_emitter import (
    _DETECTORS,
    DEFAULT_PARTITION_CAP,
    ORACLES,
    POLICIES,
    Policy,
    Segments,
    _normalized_disjoint,
    build_report,
    build_request,
    check_segment_separation,
    covering_array,
    instrument_block,
    make_seeded_fixture,
    run_case,
)

FIDE_SCHEMA_ID = "llm-shield.streaming-privacy-http-profile/v2.1.0"
FIDE_CORPUS_ID = "fide-response-split"
FIDE_CORPUS_VERSION = "0.1.0"
TRANSPORT_CONTRACT_ENV = "V2_TRANSPORT_CONTRACT_JSON"
TRANSPORT_MEASUREMENT_REVISION = "fide-transport-contract/1"


def _transport_contract_from_environment() -> dict[str, Any] | None:
    raw = os.environ.get(TRANSPORT_CONTRACT_ENV)
    if not raw:
        return None
    contract = json.loads(raw)
    if not isinstance(contract, dict):
        raise TypeError(f"{TRANSPORT_CONTRACT_ENV} must encode a JSON object")
    if contract.get("measurement_revision") != TRANSPORT_MEASUREMENT_REVISION:
        raise ValueError(
            f"{TRANSPORT_CONTRACT_ENV}.measurement_revision must be "
            f"{TRANSPORT_MEASUREMENT_REVISION!r}"
        )
    return contract


# --------------------------------------------------------------------------------------
# Axes. Five of them are v2's, unchanged and in the same order, so a reader comparing the
# two corpora is comparing one added factor and not a redesign.
# --------------------------------------------------------------------------------------

FIDE_AXES: dict[str, tuple[str, ...]] = {
    # Eight needles: v2's four PII values and the four secret fixtures. The axis is still
    # called `entity` because the schema, the by_axis slice and every downstream
    # consumer name it that; `needle_class` is what carries the new information.
    "entity": tuple(measurable_ids()),
    "needle_class": nr.classes_in_use(),
    "encoding": ("plain", "percent"),
    "fragmentation": ("single_chunk", "adversarial"),
    "carrier": ("sse-delta-content", "sse-json-field"),
    "request_site": (
        "chat-content",
        "system-content",
        "unrecognised-key",
        "tool-description",
    ),
}


def feasible(case: dict[str, str]) -> bool:
    """A needle belongs to exactly one class, so most (entity, class) pairs are impossible.

    Without this the covering array would be asked to cover
    `(entity=AWSKEYID, needle_class=pii)`, `proof_complete` could never be true, and the
    schema's coverage gate -- which forbids a pass on incomplete coverage -- would be
    permanently unsatisfiable. The constraint is expressed once, here, and both
    `_all_pairs` and `covering_array` consult it, so the required-pair count and the
    generated cases cannot disagree.
    """
    return class_of(case["entity"]) == case["needle_class"]


def fide_covering_array() -> list[dict[str, str]]:
    return covering_array(FIDE_AXES, feasible)


# --------------------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------------------


def build_fide_segments(seed: str) -> Segments:
    """Echo stays personal data; injection carries every needle.

    The echo half is the RESTORATION duty and it is unchanged: the caller's own personal
    data, masked on the way out, which the gateway must give back. Adding secrets to the
    echo would be asking a gateway to hand a credential back to its caller, which is not
    a duty anyone claims and is not a thing this study should ask for.

    The injection half is the CONTAINMENT duty and it carries all eight needles. The PII
    needles are redrawn until they are disjoint from the echo on their normalised forms,
    exactly as v2 does; the secret needles are fixed literals and are disjoint from any
    personal-data value by construction, which is asserted rather than assumed by
    `check_segment_separation`.
    """
    import random

    rng = random.Random(seed)
    echo = make_seeded_fixture(rng)
    for _ in range(64):
        injection = needle_values(seed + ":injection")
        if _normalized_disjoint(echo, injection):
            return Segments(echo=echo, injection=injection)
        # Redraw the ECHO, not the injection: the injection's secret half is fixed and
        # its PII half is pinned to the seed so the corpus stays reproducible.
        echo = make_seeded_fixture(rng)
    raise RuntimeError("could not draw an echo fixture disjoint from the needle set")


# --------------------------------------------------------------------------------------
# The detector set. PII patterns from v2, secret patterns transcribed from
# detect-secrets 1.5.0 with the source named per line.
# --------------------------------------------------------------------------------------

# TRANSCRIBED, NOT INVENTED. Each pattern is the denylist entry the named plugin
# publishes, so the reference control is measuring a documented detector definition
# rather than a regex this study wrote to make its own point. The detect-secrets version
# is pinned in `needle_registry.DetectorClaim` beside each needle.
_SECRET_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # detect_secrets.plugins.aws.AWSKeyDetector
    ("AWSKEYID", re.compile(r"(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}")),
    # detect_secrets.plugins.github_token.GitHubTokenDetector
    ("GHTOKEN", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}")),
    # detect_secrets.plugins.slack.SlackDetector
    ("SLACKBOT", re.compile(r"xox(?:a|b|p|o|s|r)-(?:\d+-)+[a-z0-9]+", re.I)),
    # detect_secrets.plugins.private_key.PrivateKeyDetector matches the HEADER, not the
    # block. Detection and redaction are different decisions and this study owns the
    # second one: having matched a header, the wrapper redacts through the matching END
    # line, or to the end of the text it can see if the END line has not arrived. That is
    # ordinary redactor behaviour and it is stated here because the alternative -- redact
    # the 31 matched characters and forward the payload -- would make the leak metric for
    # this needle measure the header alone.
    (
        "PEMBLOCK",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
            r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY-----|.*\Z)",
            re.S,
        ),
    ),
)

FIDE_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = _DETECTORS + _SECRET_DETECTORS

# The longest unit the retention controls must not split, over every needle in every
# encoding. Computed, not guessed: a retention bound stated as a constant is the defect
# the PEM needle exists to expose.
def _max_needle_length() -> int:
    from urllib.parse import quote

    lengths = []
    for needle in NEEDLES:
        if needle.value:
            lengths.append(len(needle.value))
            lengths.append(len(quote(needle.value, safe="")))
    # The seeded PII needles vary per run; their rendered lengths are bounded by the
    # percent-encoded email, which is the longest of them. A generous constant here is
    # safe -- retention that is too long costs latency, not correctness -- but it is
    # derived from a real draw rather than picked.
    drawn = make_seeded_fixture(__import__("random").Random("bound"))
    for value in drawn.values():
        lengths.append(len(quote(value, safe="")))
    return max(lengths)


MAX_NEEDLE_LENGTH = _max_needle_length()


def _redact(text: str) -> str:
    out = text
    for _id, pattern in FIDE_DETECTORS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _redact_then_rehydrate(text: str, vault: dict[str, str]) -> str:
    """Redact raw values first, then restore this gateway's own placeholders.

    Same ordering rule as v2's, and it is load-bearing for the same reason: rehydrating
    first hands the detector the caller's own data and it redacts it again, so the
    gateway destroys its own restore and scores FidelityRate 0.
    """
    out = _redact(text)
    for token, original in vault.items():
        out = out.replace(token, original)
    return out


class FidePassthrough(Policy):
    """Forwards bytes untouched. Leaks in both arms, so DeltaFrag is 0 at total failure."""

    name = "fide-passthrough"

    def feed(self, delta: str) -> str:
        return delta


class FideRedactAll(Policy):
    """Redacts everything it detects and restores nothing. The one-way anonymiser."""

    name = "fide-redact-all"
    redacts = True

    def feed(self, delta: str) -> str:
        return _redact(delta)


class FideChunkLocal(Policy):
    """The modelled defect: correct per event, blind across events."""

    name = "fide-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        return _redact_then_rehydrate(delta, self.vault)


class FideWhitespaceRetention(Policy):
    """v2's `bounded-retention`, ported VERBATIM, including its whitespace assumption.

    It holds back the tail and cuts at the last space before it. That is sound if and
    only if every protected unit is a whitespace-free run, which is true of every v2
    needle and FALSE of a PEM block. This row exists to measure what that assumption
    costs rather than to assert that it is fine, and it is the reason the FIDE corpus
    contains a needle with a space in it.
    """

    name = "fide-whitespace-retention"
    rehydrates = True
    redacts = True
    retains = True

    def __init__(self, vault: dict[str, str]) -> None:
        super().__init__(vault)
        self._buffer = ""
        self._bound = max(
            [len(t) for t in vault] + [len(v) for v in vault.values()] + [24]
        )

    def _cut(self) -> int:
        limit = len(self._buffer) - self._bound
        if limit <= 0:
            return 0
        boundary = self._buffer.rfind(" ", 0, limit)
        return boundary + 1 if boundary != -1 else 0

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = self._cut()
        if cut <= 0:
            return ""
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return _redact_then_rehydrate(safe, self.vault)

    def flush(self) -> str:
        safe, self._buffer = self._buffer, ""
        return _redact_then_rehydrate(safe, self.vault)


class FideLengthBoundedRetention(Policy):
    """Retention bounded by the longest protected unit, with no whitespace assumption.

    THE RULE, and why each half is needed.

      * Hold back the last `L-1` characters, where `L` is the longest unit that must not
        be split. Any occurrence that is INCOMPLETE in the current buffer must begin
        inside that window, so nothing incomplete is ever emitted.
      * If a COMPLETE match straddles the resulting cut, move the cut back to that
        match's start. Without this the buffer could emit the first half of a value it
        has already recognised -- the failure mode that looks like retention working
        while the value walks out in two pieces.

    Retained length is therefore at most `2L`, which is still a constant in the response
    length: the memory bound survives. `L` is `MAX_NEEDLE_LENGTH`, computed from the
    registry rather than written down as a number.
    """

    name = "fide-length-bounded-retention"
    rehydrates = True
    redacts = True
    retains = True

    def __init__(self, vault: dict[str, str]) -> None:
        super().__init__(vault)
        self._buffer = ""
        self._bound = max(
            [MAX_NEEDLE_LENGTH]
            + [len(t) for t in vault]
            + [len(v) for v in vault.values()]
        )

    def _cut(self) -> int:
        naive = len(self._buffer) - (self._bound - 1)
        if naive <= 0:
            return 0
        cut = naive
        for _id, pattern in FIDE_DETECTORS:
            for match in pattern.finditer(self._buffer):
                if match.start() < naive < match.end():
                    cut = min(cut, match.start())
        for token in self.vault:
            start = self._buffer.find(token)
            while start != -1:
                if start < naive < start + len(token):
                    cut = min(cut, start)
                start = self._buffer.find(token, start + 1)
        return max(cut, 0)

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = self._cut()
        if cut <= 0:
            return ""
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return _redact_then_rehydrate(safe, self.vault)

    def flush(self) -> str:
        safe, self._buffer = self._buffer, ""
        return _redact_then_rehydrate(safe, self.vault)


class FideRetentionPlusDecoding(FideLengthBoundedRetention):
    """Correct retention PLUS decoding before detection.

    Retention fixes fragmentation and nothing else: a percent-encoded AKIA key has no
    literal `AKIA` run for the denylist to find, however much buffer is held. Encoding
    and fragmentation are independent defects and each needs its own mitigation. This is
    the only reference control expected to contain every case in both arms.
    """

    name = "fide-retention-plus-decoding"

    def _detect_over_decodings(self, text: str) -> str:
        from urllib.parse import quote, unquote

        out = text
        decoded = unquote(text)
        if decoded == text:
            return out
        for _id, pattern in FIDE_DETECTORS:
            for match in pattern.finditer(decoded):
                original = match.group(0)
                for surface in (original, quote(original, safe=""), quote(original)):
                    out = out.replace(surface, "[REDACTED]")
        return out

    def feed(self, delta: str) -> str:
        return self._detect_over_decodings(super().feed(delta))

    def flush(self) -> str:
        return self._detect_over_decodings(super().flush())


FIDE_POLICIES: dict[str, type[Policy]] = {
    p.name: p
    for p in (
        FidePassthrough,
        FideRedactAll,
        FideChunkLocal,
        FideWhitespaceRetention,
        FideLengthBoundedRetention,
        FideRetentionPlusDecoding,
    )
}

# External targets use a label that is deliberately NOT a reference-policy name.  In
# external-gateway mode `run_case` treats the name as report metadata only, so reusing
# `fide-chunk-local` here would falsely publish the shipping gateway as a study control
# and would also give it the control's detector scope.  Keep supported needles explicit:
# SLACKBOT is absent because LLM-Shield-Proxy 1.6.0 documents no Slack-token pattern.
FIDE_EXTERNAL_TARGETS: dict[str, dict[str, Any]] = {
    "llm-shield-proxy-1.6.0-response-on": {
        "version": "1.6.0",
        "enabled": (
            "EMAIL",
            "SSN",
            "CARDPAN",
            "USPHONE",
            "AWSKEYID",
            "GHTOKEN",
            "PEMBLOCK",
        ),
        "claim_citation": (
            "LLM-Shield-Proxy 1.6.0: website/docs/features/data-protection-pii-redaction/"
            "supported-pii-types.md and the v1.6.0 Tier-1 pattern catalog, measured "
            "from the v1.6.0 response-scanning listener"
        ),
        "configuration_reference": (
            "benchmarks/fide_sweep.py stage_shield; LLM-Shield-Proxy 1.6.0 "
            "response scanning enabled on port 8813 with an operator-supplied 32-byte "
            "SHIELD_ENCRYPTION_KEY for the required stream-digest receipt"
        ),
        "scope_source": (
            "LLM-Shield-Proxy 1.6.0 documented Tier 1 catalog and TIER1_PATTERNS; "
            "SLACKBOT is not enabled because no Slack-token claim is documented"
        ),
    }
}

# `_make_gateway` resolves the policy by name out of the shared registry, so the FIDE
# controls have to be visible there. `DEFAULT_POLICIES` was frozen at v2 import time and
# is not affected, so a bare `v2_emitter` run still runs exactly the v2 set.
POLICIES.update(FIDE_POLICIES)


# --------------------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------------------


def fide_fixture_block() -> dict[str, Any]:
    """The fixture block for this corpus, covering BOTH needle classes.

    v2's default block names three PII formats and a value space drawn from the v1
    generator. Published on a FIDE row it would describe four of the eight needles and
    would state a value space the run did not use.

    The interesting field is `value_space_nominal`. The schema documents a value of 1 as
    "that entity does not vary", which is exactly the right encoding for a fixed literal:
    the four secret needles are the same string at every seed by design, and publishing 1
    for them says so in a machine-readable field rather than in prose somewhere else. It
    is also what makes the uncertainty analysis's refusal to fit a seed-level interval to
    the secret family checkable from the artefact alone.
    """

    space: dict[str, int] = {}
    formats: dict[str, str] = {}
    v1_space = {}
    try:
        from .v2_emitter import _value_space

        v1_space = _value_space()
    except Exception:  # pragma: no cover - the v1 generator is always importable here
        v1_space = {}
    for needle in NEEDLES:
        if needle.needle_class == "prompt_injection":
            continue
        if needle.generation == "fixed":
            space[needle.id] = 1
            formats[needle.id] = needle.label
        else:
            space[needle.id] = int(v1_space.get(needle.id, 1))
            formats[needle.id] = needle.label
    return {
        "varies_per_run": True,
        "values_published": False,
        "formats": formats,
        "value_space_nominal": space,
        "specimens_are_valid": (
            "PII: Luhn and SSA-range constraints enforced by make_seeded_fixture. "
            "Secrets: each fixture's validity disposition is recorded in "
            "needle_registry.Needle.validity -- one syntactically valid, two shape-valid, "
            "one deliberately invalid. The PEM payload is asserted non-key by decoding it."
        ),
        "specimens_are_non_real": (
            "PII: SSN area 900-999, RFC 2606 example.com, published test-card ranges, "
            "NANP 555-01xx. Secrets: one value published by AWS itself as its own "
            "documentation example; three fixed literals containing EXAMPLE and NOTAREAL, "
            "none of which was ever presented to the issuing service. Full provenance and "
            "the non-liveness argument per fixture are in needle_registry."
        ),
    }


def run_fide_policy(
    policy_name: str,
    seed: str,
    iterations: int = 1,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
    oracle: str = "midpoint",
    partition_cap: int = DEFAULT_PARTITION_CAP,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One policy across the whole FIDE covering array, emitted as a v2.1.0 report.

    Like `v2_emitter.run_policy`, this refuses `iterations != 1`. `run_case` keeps only
    the LAST response for leak inspection, so a multi-iteration run would silently score
    one observation while reporting the latency of several. The FIDE CLI never exposed
    `--iterations`, which is why no published report is affected; the guard closes the
    programmatic path that v2 already closes, so the two profiles fail the same way.
    """
    if iterations != 1:
        raise ValueError(
            "FIDE run_fide_policy supports exactly one response observation per case; "
            "multi-iteration leak aggregation requires a new instrument/evidence release"
        )
    segments = build_fide_segments(seed)
    cases = fide_covering_array()
    all_bodies = json.dumps(
        [build_request(segments, case) for case in cases], sort_keys=True
    )
    separation = check_segment_separation(segments, all_bodies)
    results = [
        run_case(
            segments,
            policy_name,
            case,
            iterations=iterations,
            gateway_url=gateway_url,
            upstream_port=upstream_port,
            model=model,
            oracle=oracle,
            partition_cap=partition_cap,
        )
        for case in cases
    ]

    fixture = fide_fixture_block()
    external = FIDE_EXTERNAL_TARGETS.get(policy_name)
    is_control = policy_name in FIDE_POLICIES
    claim = {
        "vendor_claims_pii_redaction": "claimed",
        # NAMES THIS MODULE, not v2's. The default cites `v2_emitter` policy docstrings,
        # which is where the v2 reference policies are defined and is not where these are.
        # A provenance field that points at the wrong module is the same defect as a row
        # citing this harness as the source of a third-party vendor's claim, which has
        # happened here before.
        "claim_citation": (
            "pii_leak_benchmark.fide_emitter policy docstrings; the secret detector "
            "patterns are transcribed from detect-secrets 1.5.0 plugin denylists and are "
            "cited per needle in needle_registry.DetectorClaim"
            if is_control
            else (
                external["claim_citation"]
                if external
                else "operator-supplied; see the run recipe in the target's profile directory"
            )
        ),
        "configured_for_this_run": True,
        "configuration_reference": (
            "pii_leak_benchmark.fide_emitter.FIDE_POLICIES[" + repr(policy_name) + "]"
            if is_control
            else (
                external["configuration_reference"]
                if external
                else "external gateway configured by the operator; see the run script"
            )
        ),
        "recorded_by": "operator",
        "request_path_redaction_configured": os.environ.get(
            "V2_REQUEST_PATH_REDACTION", "unknown"
        ),
    }
    enabled = (
        tuple(name for name, _ in FIDE_DETECTORS)
        if is_control and policy_name != "fide-passthrough"
        else tuple(external["enabled"] if external else ())
    )
    scope_source = (
        "pii_leak_benchmark.fide_emitter.FIDE_DETECTORS"
        if is_control
        else (
            external["scope_source"]
            if external
            else "no documented detector scope supplied for this external target"
        )
    )
    scope = nr.needle_scope_block(
        enabled,
        scope_source,
    )
    report = build_report(
        segments,
        results,
        separation,
        seed,
        context={
            "base_url": gateway_url or "in-process loopback reference policy",
            "model": model,
            "capture_port": upstream_port,
            "feasible": feasible,
        },
        axes=FIDE_AXES,
        corpus={"id": FIDE_CORPUS_ID, "version": FIDE_CORPUS_VERSION},
        scope=scope,
        fixture=fixture,
        claim=claim,
        schema_id=FIDE_SCHEMA_ID,
    )
    transport_contract = _transport_contract_from_environment()
    if transport_contract is not None:
        report["transport_contract"] = transport_contract
        # The scoring functions and inspector digest are unchanged. This label makes the
        # report-level provenance addition visible without falsely claiming a new scorer.
        report["harness_revision"] += "+transport-contract.1"
    if external:
        # `build_report`'s default version identifies the harness reference policy.  An
        # external product row must identify the target version instead; the harness
        # revision remains separately available in `harness_revision` and `instrument`.
        report["implementation"]["version"] = external["version"]
    # The needle registry is a corpus input the seed does not pin: the secret values are
    # fixed literals, so `corpus.seed` says nothing about them. Publishing the registry
    # digest is what lets a reader tell two generations of fixture apart.
    report["corpus"]["needle_registry_sha256"] = nr.registry_digest()
    report["corpus"]["needle_classes"] = list(nr.classes_in_use())
    report["corpus"]["needle_classes_declared_unmeasured"] = [
        cls for cls in nr.NEEDLE_CLASSES if cls not in nr.classes_in_use()
    ]

    metrics = report["metrics"]
    summary = {
        "fidelity_rate": metrics["fidelity_rate"],
        "leak_single_chunk": metrics["leak_rate"]["single_chunk"],
        "leak_adversarial": metrics["leak_rate"]["adversarial"],
        "delta_frag": metrics["delta_frag"],
        "cases_applicable": metrics["cases_applicable"],
        "cases_attempted": metrics["cases_scored"],
        "inconclusive": metrics["cases_inconclusive"],
        "echo_observable": metrics["cases_echo_observable"],
        "by_needle_class": {
            cls: metrics["by_axis"]["needle_class"].get(cls, {})
            for cls in nr.classes_in_use()
        },
        "partition_oracle": metrics["partition_oracle"],
        "by_axis_arm_needle_class": metrics["by_axis_arm"].get("needle_class", {}),
        "by_axis_arm_entity": metrics["by_axis_arm"].get("entity", {}),
        "pairs": (
            report["corpus"]["coverage"]["pairs_covered"],
            report["corpus"]["coverage"]["pairs_required"],
        ),
        "instrument": instrument_block(),
    }
    if "transport_contract" in report:
        summary["transport_contract"] = report["transport_contract"]
    return report, summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    import pathlib
    import time

    parser = argparse.ArgumentParser(description="FIDE profile emitter (draft v2.1.0)")
    parser.add_argument("--out", required=True, help="output directory (REQUIRED)")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated policy names")
    parser.add_argument("--seed", required=True, help="hex seed")
    parser.add_argument("--gateway-url", default=None)
    parser.add_argument("--upstream-port", type=int, default=0)
    parser.add_argument("--model", default="test")
    parser.add_argument("--oracle", default="midpoint", choices=list(ORACLES))
    parser.add_argument("--partition-cap", type=int, default=DEFAULT_PARTITION_CAP)
    args = parser.parse_args(argv)

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    validator: Callable[[dict[str, Any]], list[str]] | None = None
    if args.validate:
        import jsonschema

        schema = json.loads(
            pathlib.Path("spec/v2.1.0/http-profile.schema.json").read_text(
                encoding="utf-8"
            )
        )

        def validator(report: dict[str, Any]) -> list[str]:  # type: ignore[misc]
            v = jsonschema.Draft202012Validator(schema)
            return [f"{list(e.path)}: {e.message}" for e in v.iter_errors(report)]

    selected = [n.strip() for n in args.only.split(",") if n.strip()] or list(
        FIDE_POLICIES
    )
    failures = 0
    for name in selected:
        started = time.perf_counter()
        report, summary = run_fide_policy(
            name,
            seed=args.seed,
            gateway_url=args.gateway_url,
            upstream_port=args.upstream_port,
            model=args.model,
            oracle=args.oracle,
            partition_cap=args.partition_cap,
        )
        errors = validator(report) if validator else []
        write_json_artifact(outdir / f"{name}.json", report, indent=1)
        write_json_artifact(outdir / f"{name}.summary.json", summary, indent=1)
        status = "VALID" if validator and not errors else ("INVALID" if errors else "-")
        print(
            f"{name:32} fidelity={summary['fidelity_rate']:<6} "
            f"leak_single={summary['leak_single_chunk']:<6} "
            f"leak_adv={summary['leak_adversarial']:<6} "
            f"DeltaFrag={summary['delta_frag']:<7} "
            f"n={summary['cases_applicable']}/{summary['cases_attempted']:<4} "
            f"outcome={report['outcome']:<22} schema={status} "
            f"{time.perf_counter() - started:.1f}s",
            flush=True,
        )
        for err in errors[:6]:
            print("      !", err)
        failures += bool(errors)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
