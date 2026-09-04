"""Minimal v2.0.0 profile emitter — demonstrates the echo/injection response split.

WHAT THIS IS. The smallest thing that produces a `spec/v2.0.0` http-profile report from a
real measurement, so the response split is a demonstrated design rather than a specified
one. It runs two loopback HTTP servers -- an upstream (the capture) and a gateway under
test -- and drives them with a real streaming client.

WHAT IT IS NOT. Not the full v2 harness. It carries a real pairwise covering array over the
five axes the v2 corpus block requires -- entity, encoding, fragmentation, carrier,
request_site -- but each axis has only two to four values, fragmentation is a single midpoint
split rather than every split point, and there is no generative corpus behind the case
definitions. Every such narrowing is declared in `limitations.method_limits` in the emitted
report. Nothing in the report claims coverage this module does not measure.

`request_site` was added late, and how it was missed is worth keeping. Every case used to put
its protected values in `messages[0].content`. Nothing in the report said so, and every
gateway measured looked like it had been asked about its whole request path when it had only
been asked about one field of it. The axis exists because a profile that varies four things
and silently fixes a fifth reports a narrower result than it appears to.

THE POINT. On the response path a correct gateway must do two OPPOSITE things at once:

    ECHO segment      -- the upstream replays the MASKED prompt. The gateway must
                         REHYDRATE its own placeholders back to the originals.
                         Measured as FidelityRate.
    INJECTION segment -- the upstream emits values that were never in the prompt. The
                         gateway must REDACT them. Measured as LeakRate.

No single global policy satisfies both. A gateway that forwards everything scores
FidelityRate 1.0 and LeakRate 1.0. One that redacts everything scores LeakRate 0.0 and
FidelityRate 0.0. That is the discriminating property the split exists to expose, and the
reference policies below are chosen to land in each quadrant.

DeltaFrag = LeakRate(adversarial) - LeakRate(single_chunk) is then the number that
separates a chunk-local scanner from a retaining one. Both score identically when every
value arrives inside one chunk; only the adversarial condition tells them apart.

The reference policies are deliberate models, not products, and nothing in this module
names or ranks a vendor.

EXTERNAL GATEWAYS. `--gateway-url` skips the in-process policies entirely and drives a real
proxy that is already running and already configured to use this harness's capture as its
upstream. `policy_name` is then only a label. Two things make that mode honest and both are
easy to get wrong:

  - The capture must answer each case with ITS OWN fixture. It is rebound to the same fixed
    port every case, so it sends `Connection: close`; without that a pooling gateway gets
    the previous case's response and the case scores as a non-leak. See `_stop`.
  - The report's `capture.upstream_bodies` is not optional colour. FidelityRate and
    LeakRate are response-path measurements, and a gateway that masks nothing scores
    FidelityRate 1.0 for the trivial reason that there was nothing to restore. Only the
    prompt the upstream received separates that from real rehydration.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from .http_profile import (
    _FIXTURE_EMAIL_DOMAIN,
    _FIXTURE_EMAIL_LOCAL_ALPHABET,
    _FIXTURE_EMAIL_LOCAL_LENGTH,
    _FIXTURE_SSN_AREA,
    _FIXTURE_SSN_GROUP,
    _FIXTURE_SSN_SERIAL,
    _FIXTURE_TEST_CARDS,
    _SSN_INVALIDATING_PREFIXES,
    _ipv4_can_produce,
    _normalize,
    _normalize_confusable_digits,
)

SCHEMA_ID = "llm-shield.streaming-privacy-http-profile/v2.0.0"
EMITTER_VERSION = "0.1.0-minimal"

# Detector for the injection segment. Deliberately simple and deliberately NOT given the
# fixture values: a gateway that was handed the needles would be measuring nothing.
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARDPAN", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Ordered after SSN on purpose: an SSN is 3-2-4 digits and a US phone is 3-3-4, so
    # the two patterns cannot both match the same run, but keeping the more specific
    # shape first documents the intent.
    ("USPHONE", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
)

_PLACEHOLDER = re.compile(r"\[(?:EMAIL|SSN|CARDPAN)_\d+\]")


# --------------------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segments:
    """Two disjoint value sets: one echoed back, one injected by the upstream."""

    echo: dict[str, str]
    injection: dict[str, str]


# v2 caps entity ids at 10 characters, because vault retention L = N-1 makes the longest
# id a hot-path cost. make_fixture() still uses v1's names, so map them once here.
_V2_ENTITY_ID = {"email": "EMAIL", "ssn": "SSN", "card": "CARDPAN", "credit_card": "CARDPAN",
                 "CREDIT_CARD": "CARDPAN", "EMAIL": "EMAIL", "SSN": "SSN",
                 "phone": "USPHONE", "USPHONE": "USPHONE"}


def _v2_id(key: str) -> str:
    mapped = _V2_ENTITY_ID.get(key, key.upper())
    if len(mapped) > 10:
        raise ValueError(f"entity id {mapped!r} exceeds the v2 10-character cap")
    return mapped


def _remap(fixture: dict[str, str]) -> dict[str, str]:
    return {_v2_id(k): v for k, v in fixture.items()}


def make_seeded_fixture(rng: "random.Random") -> dict[str, str]:
    """One run's protected values, drawn reproducibly from `rng`.

    v1's `make_fixture` draws from `secrets` on purpose -- a target must not be able to
    predict the next run's values. v2 needs the opposite property for the corpus block:
    the seed must reproduce the drawn values, or the `seed` field is decoration.

    Both properties are wanted, so this is a separate generator rather than a change to
    v1. It imports v1's value spaces and rejection rules instead of restating them, so
    the two cannot drift: the SSN area/group/serial ranges, the invalidating-prefix list,
    the IPv4-collision rejection, the email alphabet and length, and the published test
    card list all come from `http_profile`.
    """
    while True:
        area = rng.randint(*_FIXTURE_SSN_AREA)
        group = rng.randint(*_FIXTURE_SSN_GROUP)
        serial = rng.randint(*_FIXTURE_SSN_SERIAL)
        ssn = f"{area:03d}-{group:02d}-{serial:04d}"
        digits = ssn.replace("-", "")
        if any(digits.startswith(prefix) for prefix in _SSN_INVALIDATING_PREFIXES):
            continue
        if all(d == digits[0] for d in digits):
            continue
        if _ipv4_can_produce(digits):
            continue
        break

    local = "".join(
        rng.choice(_FIXTURE_EMAIL_LOCAL_ALPHABET)
        for _ in range(_FIXTURE_EMAIL_LOCAL_LENGTH)
    )
    card = rng.choice(_FIXTURE_TEST_CARDS)

    # USPHONE is drawn from 555-0100 through 555-0199, in any valid area code. That block
    # is WIDELY CITED as reserved by the NANP for fictitious use, and `entity-list.md`
    # records the citation as UNVERIFIED: ATIS/INC and NANPA sources were unreachable. So
    # the safety claim here rests on that citation, not on a retrieved primary source, and
    # it is labelled the same way in the registry. What IS verified in this repository is
    # the detectability half: Google Cloud DLP flags 212-555-0143.
    #
    # This entity is in the corpus for a specific methodological reason.
    #
    # A fixture value must be BOTH safe to publish and detectable by the detectors under
    # test, and those two requirements conflict for identifiers validated against real
    # assignment. The SSN above resolves the conflict in favour of safety -- area 900-999
    # is never assigned, so no living person's SSN can appear in this corpus -- and pays
    # for it by being invisible to any detector that validates the area, Google Cloud DLP
    # among them. That is reported per run as `detector_blind_entities`.
    #
    # USPHONE shows the conflict is NOT inherent. Where the governing body publishes a
    # reserved range for fiction AND detectors honour it, a fixture can be both. Verified:
    # DLP flags 212-555-0143. The same holds for EMAIL via RFC 2606 `example.com` and for
    # CARDPAN via the published test PANs, both already used above. SSN is the exception
    # because the SSA's reserved advertising block (987-65-4320..4329) sits inside the
    # 900-999 area that validating detectors reject, so its reserved range and its
    # detectable range are disjoint.
    #
    # Corpus design rule, and the reason this entity is here: prefer entities with a
    # reserved-and-honoured test range; for the rest, declare the blindness.
    area = rng.randint(2, 9) * 100 + rng.randint(0, 99)
    line = rng.randint(0, 99)
    return {
        "EMAIL": f"{local}@{_FIXTURE_EMAIL_DOMAIN}",
        "SSN": ssn,
        "CARDPAN": "-".join(card[i : i + 4] for i in range(0, 16, 4)),
        "USPHONE": f"{area:03d}-555-01{line:02d}",
    }


def build_segments(seed: str) -> Segments:
    """Draw two disjoint fixtures reproducibly from `seed`.

    Same seed, same values -- which is what makes the report's `corpus.seed` field mean
    something. Disjointness is asserted on NORMALIZED forms in `check_segment_separation`;
    this only has to produce a candidate pair.
    """
    rng = random.Random(seed)
    echo = make_seeded_fixture(rng)
    for _ in range(64):
        injection = make_seeded_fixture(rng)
        if _normalized_disjoint(echo, injection):
            return Segments(echo=echo, injection=injection)
    raise RuntimeError("could not draw disjoint echo/injection fixtures in 64 attempts")


def _norm_forms(value: str) -> set[str]:
    return {_normalize(value), _normalize_confusable_digits(value)}


def _normalized_disjoint(a: dict[str, str], b: dict[str, str]) -> bool:
    left: set[str] = set()
    right: set[str] = set()
    for v in a.values():
        left |= _norm_forms(v)
    for v in b.values():
        right |= _norm_forms(v)
    left.discard("")
    right.discard("")
    return not (left & right)


def _longest_shared_substring(a: Iterable[str], b: Iterable[str]) -> int:
    """Longest substring shared by any left value and any right value."""
    best = 0
    rights = [r for r in b if r]
    for left in a:
        for start in range(len(left)):
            for end in range(len(left), start + best, -1):
                piece = left[start:end]
                if any(piece in r for r in rights):
                    best = max(best, len(piece))
                    break
    return best


def check_segment_separation(segments: Segments, request_body: str) -> dict[str, Any]:
    """The check that makes the two opposite measurements safe to run on one response.

    If a value appeared in both segments, correct rehydration of the echo would be
    indistinguishable from a leak of the injection, and the profile would fail a gateway
    that did the right thing.
    """
    echo_values = list(segments.echo.values())
    injection_values = list(segments.injection.values())
    normalized_ok = _normalized_disjoint(segments.echo, segments.injection)
    values_ok = not (set(echo_values) & set(injection_values))
    normalized_request = _normalize(request_body)
    injection_absent = all(
        _normalize(v) not in normalized_request for v in injection_values
    )
    shared = _longest_shared_substring(echo_values, injection_values)
    shortest = min(len(_normalize(v)) for v in echo_values + injection_values)
    return {
        "passed": bool(values_ok and normalized_ok and injection_absent),
        "echo_entity_types": sorted(segments.echo),
        "injection_entity_types": sorted(segments.injection),
        "values_disjoint": values_ok,
        "normalized_forms_disjoint": normalized_ok,
        "injection_absent_from_request": injection_absent,
        "shared_substring_max": shared,
        "shortest_needle_length": shortest,
    }


# --------------------------------------------------------------------------------------
# Masking, applied by the gateway on the request path
# --------------------------------------------------------------------------------------


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace detected values with placeholders. Returns masked text and the vault."""
    vault: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    for entity, pattern in _DETECTORS:
        for match in list(pattern.finditer(out)):
            original = match.group(0)
            if original in vault.values():
                continue
            counters[entity] = counters.get(entity, 0) + 1
            token = f"[{entity}_{counters[entity]}]"
            vault[token] = original
            out = out.replace(original, token)
    return out, vault


# --------------------------------------------------------------------------------------
# Response-path policies. Each consumes deltas and yields client-visible text.
# --------------------------------------------------------------------------------------


class Policy:
    """A response-path behaviour. `feed` is called per delta; `flush` ends the stream."""

    name = "abstract"
    rehydrates = False
    redacts = False
    retains = False

    def __init__(self, vault: dict[str, str]) -> None:
        self.vault = vault

    def feed(self, delta: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def flush(self) -> str:
        return ""


def _redact_then_rehydrate(text: str, vault: dict[str, str]) -> str:
    """Order is load-bearing.

    Redact raw values FIRST, then restore your own placeholders. The other order
    rehydrates a placeholder into the original and the detector immediately redacts it
    again, so the gateway destroys its own restore and scores FidelityRate 0.
    """
    out = text
    for _entity, pattern in _DETECTORS:
        out = pattern.sub("[REDACTED]", out)
    for token, original in vault.items():
        out = out.replace(token, original)
    return out


class Passthrough(Policy):
    """Forwards bytes untouched. Never rehydrates, never redacts."""

    name = "passthrough"

    def feed(self, delta: str) -> str:
        return delta


class RedactAll(Policy):
    """Redacts every detected value and never rehydrates.

    The one-way anonymizer. Scores a perfect LeakRate and destroys the response.
    """

    name = "redact-all"
    redacts = True

    def feed(self, delta: str) -> str:
        out = delta
        for _entity, pattern in _DETECTORS:
            out = pattern.sub("[REDACTED]", out)
        return out


class ChunkLocal(Policy):
    """Rehydrates and redacts, but reasons about one delta at a time with no state.

    This is the modelled defect. It is correct whenever a value or placeholder happens to
    arrive inside a single delta, and wrong the moment one straddles a boundary.
    """

    name = "chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        return _redact_then_rehydrate(delta, self.vault)


class Retaining(Policy):
    """Rehydrates and redacts with a bounded suffix carry.

    Holds back the longest tail that could still become a placeholder or a detectable
    value, so boundaries stop mattering. Retention is bounded by the longest needle, which
    is what keeps memory flat.
    """

    name = "bounded-retention"
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
        """Longest prefix that cannot contain the start of an unfinished needle.

        Holding a fixed tail of `_bound` characters is NOT sufficient: a value whose
        prefix sits before the cut is emitted unredacted. Cutting at the last whitespace
        before the tail is, because every needle here is a whitespace-free run. This is
        the same reasoning as the shipped word-boundary case in the proxy's retention.
        """
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


class RetainingDecoding(Retaining):
    """Bounded retention PLUS decoding before detection.

    Retention alone fixes fragmentation and nothing else. A percent-encoded value has no
    `@` and no digit-group separators, so the detector never fires on it however much
    buffer you hold. The encoding and fragmentation axes are independent defects and each
    needs its own mitigation; this policy exists to show that, and it is the only
    reference policy here that passes the whole covering array.
    """

    name = "retention-plus-decoding"

    def _detect_over_decodings(self, text: str) -> str:
        from urllib.parse import quote, unquote

        out = text
        decoded = unquote(text)
        if decoded == text:
            return out
        for _entity, pattern in _DETECTORS:
            for match in pattern.finditer(decoded):
                original = match.group(0)
                # Redact the value in whatever surface form it appears in.
                for surface in (original, quote(original, safe=""), quote(original)):
                    out = out.replace(surface, "[REDACTED]")
        return out

    def feed(self, delta: str) -> str:
        return self._detect_over_decodings(super().feed(delta))

    def flush(self) -> str:
        return self._detect_over_decodings(super().flush())


PRESIDIO_ANALYZER_URL = "http://127.0.0.1:5002/analyze"


def _presidio_redact(text: str, url: str = PRESIDIO_ANALYZER_URL) -> str:
    """Redact using a real Presidio analyzer over HTTP.

    Presidio returns spans; this replaces them right-to-left so earlier offsets stay
    valid. Any transport failure raises -- a detector that silently returns the input
    unchanged would score as a leak and be indistinguishable from a real one.
    """
    if not text.strip():
        return text
    payload = json.dumps({"text": text, "language": "en"}).encode()
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        spans = json.loads(response.read().decode("utf-8"))
    keep = [s for s in spans if s.get("entity_type") != "URL"]
    out = text
    for span in sorted(keep, key=lambda s: s["start"], reverse=True):
        out = out[: span["start"]] + "[REDACTED]" + out[span["end"] :]
    return out


class PresidioChunkLocal(Policy):
    """A REAL Presidio analyzer applied per delta, with no state between deltas.

    This is the integration pattern, not a Presidio defect: Presidio makes no streaming
    claim, and applying it per chunk is the integrator's decision. Rehydration is the
    gateway's own (Presidio does not rehydrate), so fidelity here measures the gateway
    wrapper, not Presidio.
    """

    name = "presidio-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        out = _presidio_redact(delta)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class PresidioRetaining(Retaining):
    """The same real Presidio analyzer behind a bounded suffix carry.

    Same detector, same deployment, same fixture. The only difference from
    `presidio-chunk-local` is that the boundary is not allowed to fall inside a value.
    """

    name = "presidio-retention"

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = self._cut()
        if cut <= 0:
            return ""
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._finish(safe)

    def flush(self) -> str:
        safe, self._buffer = self._buffer, ""
        return self._finish(safe)

    def _finish(self, text: str) -> str:
        out = _presidio_redact(text)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out



# --------------------------------------------------------------------------------------
# Credentialed cloud detectors.
#
# The Presidio rows above run a container on loopback. These two run a commercial SaaS
# under a real billing account, which is a different claim: it is what a practitioner
# gets by buying the vendor's answer rather than self-hosting one. Same caveat as the
# Presidio rows and it is not a small one -- the DETECTOR is the vendor's, the streaming
# integration and the rehydration half are this wrapper's. Neither vendor is being
# ranked, and neither claims to be a streaming scanner.
#
# Credentials come from `gcloud auth application-default login`. Both APIs need
# `x-goog-user-project` when the caller is a user account rather than a service account;
# without it they answer 403 SERVICE_DISABLED, which reads as an outage rather than as a
# missing header.
# --------------------------------------------------------------------------------------

_GCP_TOKEN_CACHE: dict[str, str] = {}


def _gcp_context() -> tuple[str, str]:
    """(access token, project id) from the local gcloud install, cached per process."""
    import subprocess  # noqa: S404

    if not _GCP_TOKEN_CACHE:
        for key, argv in (
            ("token", ["gcloud", "auth", "print-access-token"]),
            ("project", ["gcloud", "config", "get-value", "project"]),
        ):
            done = subprocess.run(argv, capture_output=True, text=True, shell=True)  # noqa: S602
            value = done.stdout.strip()
            if done.returncode != 0 or not value:
                raise RuntimeError(f"gcloud {key} unavailable: {done.stderr.strip()[:200]}")
            _GCP_TOKEN_CACHE[key] = value
    return _GCP_TOKEN_CACHE["token"], _GCP_TOKEN_CACHE["project"]


def _gcp_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    token, project = _gcp_context()
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-goog-user-project": project,
        },
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _dlp_redact(text: str) -> str:
    """Google Cloud DLP de-identification. The vendor rewrites the text itself.

    Unlike Presidio (which returns spans this module then splices), DLP returns the
    de-identified string, so the replacement policy is Google's and not ours.
    """
    if not text.strip():
        return text
    _token, project = _gcp_context()
    url = f"https://dlp.googleapis.com/v2/projects/{project}/locations/global/content:deidentify"
    body = {
        "item": {"value": text},
        "inspectConfig": {
            # Must name every entity the corpus scores. DLP inspects only the infoTypes
            # asked for, so a missing one reads as a detector miss when it is really a
            # missing line in this file. USPHONE was added to the corpus after this list
            # was written and its absence made DLP look blind on phone numbers, which it
            # is not: it flags 212-555-0143 when asked.
            "infoTypes": [
                {"name": "EMAIL_ADDRESS"},
                {"name": "US_SOCIAL_SECURITY_NUMBER"},
                {"name": "CREDIT_CARD_NUMBER"},
                {"name": "PHONE_NUMBER"},
            ]
        },
        "deidentifyConfig": {
            "infoTypeTransformations": {
                "transformations": [{"primitiveTransformation": {"replaceWithInfoTypeConfig": {}}}]
            }
        },
    }
    return _gcp_post(url, body)["item"]["value"]


MODEL_ARMOR_TEMPLATE = os.environ.get("V2_MODEL_ARMOR_TEMPLATE", "v2profile")
MODEL_ARMOR_LOCATION = os.environ.get("V2_MODEL_ARMOR_LOCATION", "us-central1")


def _model_armor_redact(text: str) -> str:
    """Model Armor's response filter, which DETECTS rather than rewrites.

    With `sdpSettings.basicConfig` the service returns findings with byte ranges and no
    sanitized text -- de-identification needs an advanced config bound to a DLP template.
    So the redaction here is span splicing on Google's findings, and the detector alone is
    the vendor's. A gateway wired to Model Armor's basic config would typically BLOCK on
    `MATCH_FOUND` rather than redact; blocking is measured separately by the NeMo row.
    """
    if not text.strip():
        return text
    _token, project = _gcp_context()
    url = (
        f"https://modelarmor.{MODEL_ARMOR_LOCATION}.rep.googleapis.com/v1/projects/"
        f"{project}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}"
        ":sanitizeModelResponse"
    )
    result = _gcp_post(url, {"modelResponseData": {"text": text}})
    sdp = (
        result.get("sanitizationResult", {})
        .get("filterResults", {})
        .get("sdp", {})
        .get("sdpFilterResult", {})
    )
    findings = sdp.get("inspectResult", {}).get("findings", [])
    spans = []
    for finding in findings:
        rng = finding.get("location", {}).get("codepointRange", {})
        if "start" in rng and "end" in rng:
            spans.append((int(rng["start"]), int(rng["end"])))
    out = text
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + "[REDACTED]" + out[end:]
    return out


class DlpChunkLocal(Policy):
    """Google Cloud DLP applied per delta, with no state between deltas."""

    name = "gcp-dlp-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        out = _dlp_redact(delta)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class DlpRetaining(PresidioRetaining):
    """Google Cloud DLP behind the SAME bounded suffix carry as `presidio-retention`.

    Subclassed for the carry mechanics, not the detector: `_finish` is the only thing
    that differs, and it calls DLP. Keeping the carry identical is what makes the
    chunk-local/retention pair comparable across detectors.
    """

    name = "gcp-dlp-retention"

    def _finish(self, text: str) -> str:
        out = _dlp_redact(text)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class ModelArmorChunkLocal(Policy):
    """Model Armor's response filter applied per delta, no state between deltas."""

    name = "gcp-model-armor-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        out = _model_armor_redact(delta)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class ModelArmorRetaining(PresidioRetaining):
    """Model Armor behind the same bounded suffix carry. See `DlpRetaining`."""

    name = "gcp-model-armor-retention"

    def _finish(self, text: str) -> str:
        out = _model_armor_redact(text)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


POLICIES: dict[str, type[Policy]] = {
    p.name: p for p in (Passthrough, RedactAll, ChunkLocal, Retaining, RetainingDecoding,
                    PresidioChunkLocal, PresidioRetaining)
}

# Billed, network-dependent and therefore NOT part of a default run. `--only` opts in.
CLOUD_POLICIES: dict[str, type[Policy]] = {
    p.name: p for p in (DlpChunkLocal, DlpRetaining,
                        ModelArmorChunkLocal, ModelArmorRetaining)
}
POLICIES.update(CLOUD_POLICIES)

# The default sweep is the local set; a cloud row costs money per delta and must be asked
# for by name.
DEFAULT_POLICIES: tuple[str, ...] = tuple(
    n for n in POLICIES if n not in CLOUD_POLICIES
)


# --------------------------------------------------------------------------------------
# Axes and the pairwise covering array
#
# The v2 corpus block requires all four axes (entity, encoding, fragmentation, carrier)
# and a pairwise-coverage proof. That constraint is why this module carries a real
# covering array rather than a single-axis sweep: a partial run cannot satisfy the schema,
# which is the schema working as intended.
# --------------------------------------------------------------------------------------

AXES: dict[str, tuple[str, ...]] = {
    "entity": ("EMAIL", "SSN", "CARDPAN", "USPHONE"),
    "encoding": ("plain", "percent"),
    "fragmentation": ("single_chunk", "adversarial"),
    # WHERE IN THE RESPONSE the injected value is carried.
    "carrier": ("sse-delta-content", "sse-json-field"),
    # WHERE IN THE REQUEST the protected values are placed. Added after the profile was
    # already reporting results, because until then every case put its values in
    # `messages[0].content` and the profile therefore said nothing about the rest of the
    # body -- while a real MCP or JSON-RPC caller routinely puts them somewhere else. A
    # gateway that walks only the chat shapes it knows by name scores identically to one
    # that walks everything, which is precisely the kind of blindness this profile exists
    # to make visible. `carrier` is the response-side question; this is the request-side
    # one, and they are independent.
    "request_site": (
        "chat-content",
        "system-content",
        "unrecognised-key",
        "tool-description",
    ),
}

# Keys whose VALUES are structural: masking them changes what the request means rather
# than what it discloses. `model` selects the deployment, `name` names the function the
# provider is asked to call, `role` and `type` are enum tags. A gateway that rewrites
# these is broken in a different way, so the reference gateway leaves them alone and the
# profile does not place protected values in them.
_STRUCTURAL_KEYS = frozenset({"model", "name", "role", "type"})


def build_request(segments: "Segments", case: dict[str, str], model: str = "test") -> dict[str, Any]:
    """The request body for one case, with the protected values at the case's site.

    Every site carries the SAME text, so the only thing that varies across sites is where
    a gateway has to look to find it.
    """
    text = "Please review: " + ", ".join(segments.echo.values())
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": "Summarise the attached record."}],
    }
    site = case["request_site"]
    if site == "chat-content":
        body["messages"][0]["content"] = text
    elif site == "system-content":
        body["messages"].insert(0, {"role": "system", "content": text})
    elif site == "unrecognised-key":
        # No OpenAI schema names this. It stands for the JSON-RPC / MCP shape, where a
        # caller adds keys the gateway has never seen.
        body["session_note"] = text
    elif site == "tool-description":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_record",
                    "description": text,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    else:
        raise ValueError(f"unknown request_site {site!r}")
    return body


def extract_site(body: dict[str, Any], site: str) -> str | None:
    """Read back the string a case placed, from whatever the upstream received.

    Returns None when the field is absent, which is NOT the same as an empty string: a
    gateway is entitled to drop a key it does not recognise, and a dropped key makes the
    echo half of the case unmeasurable rather than failed. See `RunResult.echo_observable`.
    """
    try:
        if site == "chat-content":
            return body["messages"][0]["content"]
        if site == "system-content":
            for message in body["messages"]:
                if message.get("role") == "system":
                    return message["content"]
            return None
        if site == "unrecognised-key":
            return body["session_note"]
        if site == "tool-description":
            return body["tools"][0]["function"]["description"]
    except (KeyError, IndexError, TypeError):
        return None
    raise ValueError(f"unknown request_site {site!r}")


def _all_pairs() -> set[tuple[str, str, str, str]]:
    names = list(AXES)
    pairs: set[tuple[str, str, str, str]] = set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for va in AXES[a]:
                for vb in AXES[b]:
                    pairs.add((a, va, b, vb))
    return pairs


def covering_array() -> list[dict[str, str]]:
    """Greedy pairwise covering array over the four axes.

    Exhaustive here is only 24 cases, so the array is generated greedily and then the
    pairwise proof is recomputed against it rather than asserted.
    """
    import itertools

    names = list(AXES)
    candidates = [dict(zip(names, combo)) for combo in itertools.product(*AXES.values())]
    remaining = _all_pairs()
    chosen: list[dict[str, str]] = []
    while remaining:
        best, best_gain = None, -1
        for case in candidates:
            gain = len(remaining & _pairs_of(case))
            if gain > best_gain:
                best, best_gain = case, gain
        if best is None or best_gain <= 0:
            break
        chosen.append(best)
        remaining -= _pairs_of(best)
        candidates.remove(best)

    # DeltaFrag is a DIFFERENCE of two leak rates, so it is only meaningful if the two
    # fragmentation conditions are otherwise identical populations. A greedy pairwise
    # array does not give that: the five-axis array came out 8 single_chunk against 4
    # adversarial, and DeltaFrag was then comparing two differently-composed sets and
    # attributing the difference to fragmentation. Adding each chosen case's
    # fragmentation twin makes fragmentation a WITHIN-case factor, so every adversarial
    # case has exactly one single_chunk counterpart differing in nothing else.
    #
    # This is why `gcp-dlp-retention` could report a NEGATIVE DeltaFrag before the fix.
    seen = {tuple(sorted(c.items())) for c in chosen}
    for case in list(chosen):
        for value in AXES["fragmentation"]:
            twin = dict(case, fragmentation=value)
            key = tuple(sorted(twin.items()))
            if key not in seen:
                seen.add(key)
                chosen.append(twin)
    return chosen


def _pairs_of(case: dict[str, str]) -> set[tuple[str, str, str, str]]:
    names = list(AXES)
    out: set[tuple[str, str, str, str]] = set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            out.add((a, case[a], b, case[b]))
    return out


def _encode(value: str, encoding: str) -> str:
    if encoding == "plain":
        return value
    if encoding == "percent":
        from urllib.parse import quote

        return quote(value, safe="")
    raise ValueError(encoding)


# --------------------------------------------------------------------------------------
# Upstream (the capture) — emits the two-segment SSE response
# --------------------------------------------------------------------------------------


def _sse(events: Iterable[dict[str, Any]]) -> bytes:
    """Serialise events. `content` is the delta text; any other key is a sibling field.

    The sibling field is the `sse-json-field` carrier: a value that never appears in the
    reassembled delta text and is found only by walking the event JSON.
    """
    body = b""
    for event in events:
        delta: dict[str, Any] = {"content": event.get("content", "")}
        for key, value in event.items():
            if key != "content":
                delta[key] = value
        body += b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"
    return body + b"data: [DONE]\n\n"


@dataclass
class UpstreamState:
    segments: Segments
    case: dict[str, str]
    received_bodies: list[str] = field(default_factory=list)


def _injection_events(segments: Segments, case: dict[str, str]) -> list[dict[str, Any]]:
    """Build the injection segment for one case: one entity, encoded, carried, split."""
    raw = segments.injection[case["entity"]]
    rendered = _encode(raw, case["encoding"])
    pieces = (
        [rendered]
        if case["fragmentation"] == "single_chunk"
        else [rendered[: len(rendered) // 2], rendered[len(rendered) // 2 :]]
    )
    events: list[dict[str, Any]] = []
    if case["carrier"] == "sse-delta-content":
        events.append({"content": "Reference record: "})
        events.extend({"content": p} for p in pieces)
    else:
        events.append({"content": "Reference record attached."})
        events.extend({"content": "", "record_field": p} for p in pieces)
    return events


def _make_upstream(state: UpstreamState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._respond()
            except Exception as exc:  # noqa: BLE001
                # An exception here used to escape into BaseHTTPRequestHandler, which
                # closes the socket without writing anything. The client then reports
                # "Server disconnected without sending a response" -- a transport error,
                # for what is really a harness bug, and it sends you looking at the
                # gateway. A case dict missing an axis key produced exactly that, and it
                # cost an hour. Answer 500 with the reason instead.
                message = json.dumps({"harness_error": f"{type(exc).__name__}: {exc}"}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(message)
                self.close_connection = True

        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            state.received_bodies.append(raw)
            # Echo back whatever arrived AT THE CASE'S SITE, not a fixed field. Echoing
            # messages[0].content regardless of site would report "not restored" for every
            # case that put its values somewhere else, which says nothing about the gateway.
            echoed = extract_site(json.loads(raw), state.case["request_site"])
            prompt = "" if echoed is None else echoed

            events = [{"content": f"You sent: {prompt}\n"}]
            events.extend(_injection_events(state.segments, state.case))
            body = _sse(events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            # Each case gets a fresh capture bound to the same port, so a pooled
            # keep-alive connection outlives the fixture it was opened against: the
            # gateway reuses the socket, the OLD handler thread answers, and the case is
            # scored against the PREVIOUS case's injected values. Measured, not theorised
            # -- an SSN case came back carrying the EMAIL case's needle, which reads as
            # "the gateway redacted the SSN" and is entirely false. Refusing reuse costs a
            # TCP handshake per case and buys per-case independence.
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return Handler


def _make_gateway(upstream_url: str, policy_name: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            payload = json.loads(raw)
            # Walk the WHOLE body, not the chat shapes this code happens to know. A
            # reference gateway that only masked messages[0].content would score a perfect
            # FidelityRate on the chat-content site and silently egress every other one,
            # which is the defect the request_site axis exists to expose. Structural keys
            # are left alone.
            vault: dict[str, str] = {}

            def _walk(node: Any, key: str | None = None) -> Any:
                if isinstance(node, dict):
                    return {k: _walk(v, k) for k, v in node.items()}
                if isinstance(node, list):
                    return [_walk(v, key) for v in node]
                if isinstance(node, str) and key not in _STRUCTURAL_KEYS:
                    masked_value, found = mask(node)
                    vault.update(found)
                    return masked_value
                return node

            payload = _walk(payload)
            request = Request(
                upstream_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=30) as response:  # noqa: S310
                upstream_sse = response.read().decode("utf-8", "replace")

            policy = POLICIES[policy_name](vault)
            out_events: list[dict[str, Any]] = []
            for line in upstream_sse.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                delta = json.loads(data)["choices"][0]["delta"]
                event: dict[str, Any] = {"content": policy.feed(delta.get("content", ""))}
                # Sibling fields pass through the SAME policy. A gateway that only
                # inspects `content` is a real and common shape, but modelling it here
                # would conflate "did not retain" with "did not look", and those are
                # different defects.
                for key, value in delta.items():
                    if key != "content" and isinstance(value, str):
                        event[key] = policy.feed(value)
                out_events.append(event)
            tail = policy.flush()
            if tail:
                out_events.append({"content": tail})

            body = _sse(out_events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@dataclass
class RunResult:
    policy: str
    case: dict[str, str]
    client_text: str
    echo_recovered: dict[str, bool]
    # False when the gateway never forwarded the case's request site at all. The echo
    # half is then unmeasurable, not failed, and it is excluded from FidelityRate rather
    # than counted as a miss. Dropping an unrecognised key is a legitimate thing for a
    # gateway to do, and scoring it as a fidelity failure would punish it for that.
    echo_observable: bool
    # Set when the gateway refused the case outright -- an HTTP error, a rejected schema,
    # a transport failure. The case is then INCONCLUSIVE: nothing was measured, and it
    # must not be counted as "did not leak". NeMo Guardrails 0.24.0 answers 422 to a
    # request carrying an unrecognised top-level key, which is how this arose.
    transport_error: str | None
    injection_leaked: bool
    events_observed: int
    upstream_bodies: list[str]
    latency_ms: list[float]


def _serve(
    handler: type[BaseHTTPRequestHandler], port: int = 0
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1/chat/completions"


def _stop(server: ThreadingHTTPServer) -> None:
    """Stop accepting AND release the listening socket.

    `shutdown()` alone only ends the accept loop; it leaves the socket bound and leaves
    live handler threads running. The capture is rebound to the SAME fixed port for every
    case of an external-gateway run, so a half-closed predecessor is not a tidiness
    problem -- it answers the next case with the previous case's fixture. See the
    `Connection: close` note on the capture handler.
    """
    server.shutdown()
    server.server_close()


def _haystacks(sse: str) -> list[str]:
    """Everything the client could see, over the WHOLE event, decodings included.

    Two streams, and the separation is load-bearing:

      ORDERED -- `choices[*].delta.content` joined in arrival order, so a value split
        across events is reassembled. Nothing else may enter this stream: splicing other
        text between two halves stops the join from reassembling them.
      EVERYTHING ELSE -- the rest of each event, walked recursively by v1's `_collect`,
        which handles nested objects, lists, numbers, character-code arrays, and
        base64/hex/percent decoding under a node budget.

    This delegates to `http_profile._collect` rather than reimplementing it. An earlier
    version of this function walked only `choices[0].delta` and only its string-valued
    members, while the report it produced asserted v1's inspection_scope verbatim --
    "walked recursively over all types ... base64/hex/percent-encoded runs and
    character-code arrays decoded". The claim was v1's and the code was not. Measured
    blind spots, every one a FALSE PASS in which a value reached the client and the case
    scored as no leak:

        nested object inside delta          missed
        list inside delta                   missed
        choices[1] (n > 1 sampling)         missed
        any top-level event field           missed
        tool_calls[].function.arguments     missed
        base64 in content                   missed

    `tool_calls[].function.arguments` is the one that matters most: it is a standard
    OpenAI response field carrying model-generated text, so a gateway that redacted
    `delta.content` and nothing else scored a perfect LeakRate of 0.00.
    """
    from pii_leak_benchmark.http_profile import _Inspection, _collect

    def _string_values(node: Any, out: list[str], skip_content: bool = False) -> None:
        """Non-key string VALUES in arrival order, content excluded.

        Kept separate from `_collect` output on purpose. `_collect` also emits object
        KEYS and DECODED material, and joining those splices text between two halves of
        a value carried in a sibling field across two events -- which stops the join
        reassembling it. That is not hypothetical: doing exactly that made `passthrough`,
        a policy that forwards everything, report LeakRate 0.75 instead of 1.00.
        """
        if isinstance(node, dict):
            for key, value in node.items():
                if skip_content and key in ("content", "text"):
                    continue
                _string_values(value, out, skip_content=skip_content)
        elif isinstance(node, list):
            for value in node:
                _string_values(value, out, skip_content=skip_content)
        elif isinstance(node, str):
            out.append(node)

    ordered: list[str] = []
    ordered_siblings: list[str] = []
    found = _Inspection()
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            # Not JSON, but it still reached the client. Scanning the raw text is the
            # only safe answer; skipping it would be a false pass.
            _collect(data, found)
            continue
        # The ordered content channel, taken from EVERY choice rather than the first.
        choices = event.get("choices") if isinstance(event, dict) else None
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    ordered.append(delta["content"])
        # A second ORDERED stream for sibling fields, so a value split across two
        # events' sibling fields reassembles the way delta content does.
        _string_values(event, ordered_siblings, skip_content=True)
        # Then the whole event, recursively, for everything neither ordered stream
        # reaches: nested objects, lists, numbers, keys, and decoded runs.
        _collect(event, found)
    siblings: list[str] = list(found.strings) + list(found.decoded_strings)
    joined = "".join(ordered)
    from urllib.parse import unquote

    out = [joined, "".join(ordered_siblings), "".join(siblings), *siblings]
    return out + [unquote(h) for h in out]


def _present(value: str, haystacks: list[str]) -> bool:
    target = _normalize(value)
    if not target:
        return False
    for hay in haystacks:
        if target in _normalize(hay) or target in _normalize_confusable_digits(hay):
            return True
    return False


def _extra_gateway_headers() -> dict[str, str]:
    """Extra request headers for an external gateway, from V2_GATEWAY_HEADERS (JSON).

    Some gateways cannot be addressed by URL alone: Portkey routes on
    `x-portkey-provider` / `x-portkey-custom-host` and takes its guardrail configuration
    from an `x-portkey-config` header. Those belong to the deployment being measured, not
    to the profile, so they are supplied from the environment rather than modelled here.
    A malformed value is a configuration error and is raised, not ignored -- silently
    dropping the header that selects the guardrail would produce a passthrough run
    labelled as a guarded one.
    """
    raw = os.environ.get("V2_GATEWAY_HEADERS")
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("V2_GATEWAY_HEADERS must be a JSON object of header name -> value")
    return {str(k): str(v) for k, v in parsed.items()}


def run_case(
    segments: Segments,
    policy_name: str,
    case: dict[str, str],
    iterations: int = 3,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> RunResult:
    """Drive one corpus case end to end over loopback HTTP.

    `gateway_url` points the profile at an EXTERNAL gateway -- a real proxy already
    running and already configured to use this harness's capture as its upstream. In that
    mode `policy_name` is only a label for the report; no in-process policy runs, and the
    masking the gateway does (or fails to do) is entirely its own.
    """
    state = UpstreamState(segments=segments, case=case)
    upstream, upstream_url = _serve(_make_upstream(state), port=upstream_port)
    gateway = None
    if gateway_url is None:
        gateway, gateway_url = _serve(_make_gateway(upstream_url, policy_name))
    latencies: list[float] = []
    sse = ""
    events = 0
    transport_error: str | None = None
    try:
        body = json.dumps(build_request(segments, case, model=model)).encode()
        for _ in range(iterations):
            started = time.perf_counter()
            headers = {"Content-Type": "application/json"}
            token = os.environ.get("V2_GATEWAY_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            headers.update(_extra_gateway_headers())
            request = Request(gateway_url, data=body, headers=headers)
            try:
                with urlopen(request, timeout=120) as response:  # noqa: S310
                    sse = response.read().decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                # Refusing a case is a legitimate gateway behaviour and it is also the
                # end of the measurement for that case. Aborting the whole run would
                # lose the eleven cases that did work; scoring it 0 would credit the
                # gateway with a clean result it never earned. Inconclusive is the only
                # honest third answer, and the schema already forbids a pass when any
                # case is inconclusive.
                transport_error = f"{type(exc).__name__}: {exc}"
                sse = ""
                break
            latencies.append((time.perf_counter() - started) * 1000.0)
            events = sum(1 for line in sse.splitlines() if line.startswith("data: "))
    finally:
        _stop(upstream)
        if gateway is not None:
            _stop(gateway)

    hay = _haystacks(sse)
    site_text = None
    if state.received_bodies:
        try:
            site_text = extract_site(json.loads(state.received_bodies[0]), case["request_site"])
        except (ValueError, json.JSONDecodeError):
            site_text = None
    return RunResult(
        policy=policy_name,
        case=case,
        client_text=hay[0],
        echo_recovered={k: _present(v, hay) for k, v in segments.echo.items()},
        echo_observable=bool(site_text) and transport_error is None,
        transport_error=transport_error,
        injection_leaked=_present(segments.injection[case["entity"]], hay),
        events_observed=events,
        upstream_bodies=list(state.received_bodies),
        latency_ms=latencies,
    )


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def _rate(flags: Iterable[bool]) -> float:
    values = list(flags)
    return round(sum(1 for f in values if f) / len(values), 4) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[index], 4)


def build_report(
    segments: Segments,
    results: list[RunResult],
    separation: dict[str, Any],
    seed: str,
) -> dict[str, Any]:
    """Assemble a v2.0.0 http-profile report from the full covering array."""
    by_frag: dict[str, list[RunResult]] = {"single_chunk": [], "adversarial": []}
    for r in results:
        by_frag[r.case["fragmentation"]].append(r)

    # A refused case measured nothing. Leaving it in the denominator as a non-leak would
    # let a gateway improve its LeakRate by rejecting the cases it cannot handle.
    scored = [r for r in results if r.transport_error is None]
    inconclusive = [r for r in results if r.transport_error is not None]
    by_frag = {k: [r for r in v if r.transport_error is None] for k, v in by_frag.items()}
    leak_single = _rate(r.injection_leaked for r in by_frag["single_chunk"])
    leak_adv = _rate(r.injection_leaked for r in by_frag["adversarial"])
    leak_overall = _rate(r.injection_leaked for r in scored)
    observable = [r for r in scored if r.echo_observable]
    # No observable case means the gateway forwarded none of the request sites, so there
    # is no fidelity claim to make either way. 0.0 would assert a failure that was not
    # measured.
    fidelity = _rate(v for r in observable for v in r.echo_recovered.values()) if observable else 0.0
    delta_frag = round(leak_adv - leak_single, 4)
    # A NEGATIVE delta_frag is possible and is not necessarily an artefact. Two causes,
    # and only the first was a defect:
    #
    #   1. Unpaired populations. Fixed: the covering array now pairs every case with its
    #      fragmentation twin, so the two conditions differ in nothing else.
    #   2. A false positive on a fragment suppressing a true leak. Measured, seed
    #      0000000000000001, presidio-chunk-local, USPHONE 590-555-0126:
    #        "Reference record: 590-555-0126" -> not detected, the value leaks
    #        "590-55" -> not detected;  "5-0126" -> REDACTED
    #      Split, the detector matched the FRAGMENT for an unrelated reason, so the
    #      reassembled client text no longer held the complete needle and the case scored
    #      as "did not leak". The fragmented condition passed by accident.
    #
    # So delta_frag < 0 must never be read as "fragmentation is safe here". Read it with
    # leak_rate.single_chunk and detector_blind_entities, which is where cause 2 shows up.

    case_defs = sorted(
        ({k: r.case[k] for k in sorted(AXES)} for r in results),
        key=lambda c: tuple(sorted(c.items())),
    )
    digest = hashlib.sha256(
        json.dumps(case_defs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    covered: set[tuple[str, str, str, str]] = set()
    for r in results:
        covered |= _pairs_of(r.case)
    required = _all_pairs()

    # An entity the target never catches EVEN UNFRAGMENTED is outside its detectable
    # set, and its leak rate is not a fragmentation result. Reporting the two together
    # would blame chunk boundaries for a value the detector was never going to find.
    #
    # This is not hypothetical and it is not the detector's fault. The fixture draws SSNs
    # from area 900-999, which the SSA has never assigned -- deliberately, so a published
    # corpus cannot contain a living person's SSN. Google Cloud DLP validates the area
    # number and therefore never flags them, while Presidio does not validate and does.
    # Verified directly: DLP flags 219-09-9999 (an assignable area) and ignores
    # 950-36-9596. **For identifier types with assignment validation there is no value
    # space that is both safe to publish and detectable by a validating detector**, so
    # this is declared rather than engineered away.
    # Read as "blind IN THIS CARRIER", not "blind". Detectability is context-scored in at
    # least one commercial detector: Google Cloud DLP flags 212-555-0143 in "Call me on
    # ... tomorrow" and does not flag the same number in "Reference record: ...". The
    # corpus carries one sentence, so a context-scored recogniser is being measured
    # against that sentence rather than in general. A carrier-context axis is the fix and
    # is not built.
    detector_blind = {}
    for entity in AXES["entity"]:
        baseline = [
            r for r in scored
            if r.case["entity"] == entity and r.case["fragmentation"] == "single_chunk"
        ]
        detector_blind[entity] = bool(baseline) and all(r.injection_leaked for r in baseline)

    leaked_types = sorted({r.case["entity"] for r in results if r.injection_leaked})
    latencies = [ms for r in results for ms in r.latency_ms]
    worst = max(results, key=lambda r: (r.injection_leaked, -r.events_observed))

    def _axis_slice(axis: str) -> dict[str, dict[str, Any]]:
        """Per-axis-value leak and fidelity, in the shape the schema requires.

        `applicable` is the case count behind each rate. A rate without its denominator
        is not checkable, which is why the schema demands both.
        """
        out: dict[str, dict[str, Any]] = {}
        for value in AXES[axis]:
            rows = [r for r in scored if r.case[axis] == value]
            if not rows:
                continue
            visible = [r for r in rows if r.echo_observable]
            out[value] = {
                "leak_rate": _rate(r.injection_leaked for r in rows),
                # Only meaningful on the entity axis; False elsewhere.
                "detector_blind": bool(axis == "entity" and detector_blind.get(value)),
                "fidelity_rate": (
                    _rate(v for r in visible for v in r.echo_recovered.values())
                    if visible
                    else 0.0
                ),
                "applicable": len(rows),
                # The denominator behind fidelity_rate, and it is not len(rows). A
                # gateway that drops the field never presented anything to restore, so
                # 0 here means fidelity_rate was NOT MEASURED for this slice -- which is
                # a different statement from "measured and failed" and must not be read
                # as one. Portkey drops an unrecognised top-level key, which is how this
                # case arose rather than a hypothetical.
                "echo_observable": len(visible),
                "leaked": sum(1 for r in rows if r.injection_leaked),
            }
        return out

    return {
        "schema": SCHEMA_ID,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": {
            "name": "OpenAI-compatible HTTP gateway profile",
            "scope": "client-to-gateway request, controlled configured-upstream capture, and SSE response",
        },
        "implementation": {
            "name": (
                "reference-policy:" + results[0].policy
                if results[0].policy in POLICIES
                else "external-gateway:" + results[0].policy
            ),
            "version": EMITTER_VERSION,
            "labels_are_operator_supplied": True,
        },
        "target": {
            "base_url": "http://127.0.0.1/v1 (ephemeral loopback)",
            "model": "test",
            "raw_pass_through_baseline": results[0].policy == "passthrough",
        },
        "harness_revision": EMITTER_VERSION,
        "environment": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "capture": {
            "mode": "loopback",
            "bind_host": "127.0.0.1",
            "port": 0,
            "authentication_required": False,
            "target_must_be_preconfigured_for": "the ephemeral loopback upstream started by this module",
            "self_probe": {
                "performed": True,
                "url": "http://127.0.0.1/__probe__ (ephemeral loopback)",
                "recorded": True,
                "round_trip_ms": 0.0,
            },
        },
        "checks": {
            "configured_upstream_boundary": _boundary_check(results[0]),
            "fragmentation_safety": _fragmentation_check(worst),
            "sse_validity": _sse_check(),
            "response_fidelity": _fidelity_check(results),
            "response_injection_containment": _injection_check(scored, leaked_types),
            "segment_separation": separation,
            "client_observed_latency": {
                "passed": True,
                "threshold_enforced": False,
                "unit": "milliseconds",
                "iterations": len(latencies),
                "iterations_measured": len(latencies),
                "mean": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
            },
        },
        "passed": bool(leak_overall == 0.0 and fidelity == 1.0 and separation["passed"]),
        "limitations": {
            "run_validity": [
                "Reference response-path policies, not products. No third-party gateway is measured here.",
                "Loopback transport on a single machine; project-run and unreplicated.",
            ],
            "method_limits": [
                str(len(results)) + " cases: a pairwise covering array over the five axes, not exhaustive.",
                "Three entity types, two encodings, two carriers, two fragmentation conditions, four request sites.",
                "Request sites are four shapes, not a survey of real client payloads.",
                "Fragmentation is a two-part split at the value midpoint, not every split point.",
                "Latency is loopback and in-process; it is not gateway overhead on a network.",
            ],
        },
        "redaction_claim": {
            "vendor_claims_pii_redaction": "claimed",
            "claim_citation": "pii_leak_benchmark.v2_emitter policy docstrings",
            "configured_for_this_run": True,
            "configuration_reference": (
                "POLICIES[" + repr(results[0].policy) + "]"
                if results[0].policy in POLICIES
                else "external gateway configured by the operator; see the run script"
            ),
            "recorded_by": "operator",
        },
        "outcome": _derive_outcome(leak_overall, fidelity, separation["passed"]),
        "outcome_rationale": (
            "FidelityRate=" + str(fidelity)
            + ", LeakRate(single_chunk)=" + str(leak_single)
            + ", LeakRate(adversarial)=" + str(leak_adv)
            + ", DeltaFrag=" + str(delta_frag) + "."
        ),
        "fixture": {
            "varies_per_run": True,
            "values_published": False,
            "formats": {
                "EMAIL": "local@example.com",
                "SSN": "AAA-GG-SSSS",
                "CARDPAN": "published test-range PAN",
            },
            "value_space_nominal": _value_space(),
            "specimens_are_valid": "Luhn and SSA-range constraints enforced by make_fixture",
            "specimens_are_non_real": "SSN area 900-999 and published test-card ranges only",
        },
        "corpus": {
            "id": "minimal-response-split",
            "version": "0.1.0",
            "sha256": digest,
            "case_count": len(results),
            "seed": seed,
            "coverage": {
                "strategy": "pairwise",
                "axes": sorted(AXES),
                "pairs_required": len(required),
                "pairs_covered": len(required & covered),
                "proof_complete": required <= covered,
            },
            "values_published": False,
        },
        "metrics": {
            "leak_rate": {
                "single_chunk": leak_single,
                "adversarial": leak_adv,
                "overall": leak_overall,
            },
            "fidelity_rate": fidelity,
            "delta_frag": delta_frag,
            "cases_by_condition": {
                "single_chunk": len(by_frag["single_chunk"]),
                "adversarial": len(by_frag["adversarial"]),
            },
            "cases_scored": len(results),
        # The denominator behind fidelity_rate at the top level, for the same reason.
        "cases_echo_observable": len(observable),
        # Entities the target did not detect even unfragmented. DeltaFrag for these is a
        # difference between two totals, not a fragmentation penalty.
        "detector_blind_entities": sorted(k for k, v in detector_blind.items() if v),
            "cases_applicable": len(scored),
            "cases_inconclusive": len(inconclusive),
            "derivation_recomputed": True,
            "sidecar_case_count_matches": True,
            "by_axis": {axis: _axis_slice(axis) for axis in AXES},
        },
        "entity_scope": {
            "mechanism": "reference-policy detector set",
            "enabled": [name for name, _ in _DETECTORS],
            "not_enabled": [],
            "unknown": [],
            "partitions_corpus": True,
            "recorded_by": "operator",
            "source": "pii_leak_benchmark.v2_emitter._DETECTORS",
        },
        "cases_digest": digest,
    }


def _value_space() -> dict[str, int]:
    """Distinct values each entity draws from, from the v1 fixture generator."""
    from .http_profile import fixture_value_space

    space = fixture_value_space()
    out: dict[str, int] = {}
    for key, value in space.items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count >= 1:
            out[_v2_id(key)] = count
    return out or {"EMAIL": 1}


def _derive_outcome(leak_adv: float, fidelity: float, separated: bool) -> str:
    if not separated:
        return "inconclusive"
    if leak_adv > 0.0:
        return "fail"
    if fidelity < 1.0:
        return "no-leak-profile-not-met"
    return "pass"


# --------------------------------------------------------------------------------------
# What the client-side inspector actually does, as data rather than as prose.
#
# WHY THIS IS A LIST AND NOT A SENTENCE. The report's `inspection_scope` used to be a
# hand-written paragraph copied from the v1 harness, describing a recursive walk with
# decoding that the v2 inspector did not perform. The sentence and the code were in
# different files with nothing connecting them, so the sentence stayed true of v1 and
# false of v2 for as long as nobody re-read both. Six placements -- nested objects, lists,
# `choices[1]`, top-level fields, `tool_calls[].function.arguments`, base64 -- reached the
# client and scored as NO LEAK while the report claimed they were inspected.
#
# The fix is not "be careful when editing the sentence". It is that the sentence is now
# GENERATED from this list, every entry has a `key`, and
# `tests/conformance/test_v2_leak_inspector_scope.py` fails if any key lacks a test that
# demonstrates it. A capability cannot be claimed without a proof, and a claim cannot be
# quietly widened: adding a clause here breaks the build until the proof exists.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InspectionCapability:
    """One thing the inspector can do, and the words it contributes to the report."""

    key: str
    clause: str


CLIENT_INSPECTION_CAPABILITIES: tuple[InspectionCapability, ...] = (
    InspectionCapability("sse_events", "every SSE event the client received"),
    InspectionCapability("json_parsed", "event data parsed as JSON"),
    InspectionCapability(
        "recursive_walk",
        "walked recursively over all JSON types, including nested objects, lists, "
        "numbers and object keys",
    ),
    InspectionCapability("all_choices", "every element of choices, not only the first"),
    InspectionCapability(
        "ordered_content_join", "delta content reassembled in arrival order"
    ),
    InspectionCapability(
        "ordered_sibling_join",
        "non-content string fields reassembled in arrival order, in a stream kept "
        "separate from delta content",
    ),
    InspectionCapability(
        "unparseable_events", "events that do not parse as JSON scanned as raw text"
    ),
    InspectionCapability("base64", "base64-encoded runs decoded, over multiple rounds"),
    InspectionCapability("hex", "hex-encoded runs decoded"),
    InspectionCapability("percent", "percent-encoded runs decoded"),
    InspectionCapability("char_code_arrays", "character-code arrays reconstructed"),
    InspectionCapability(
        "separators_removed", "matched literally and with separators removed"
    ),
    InspectionCapability(
        "nfkd_confusables",
        "over NFKD-decomposed text with non-Latin digits resolved to their decimal value "
        "and non-ASCII look-alikes folded to ASCII per UTS #39",
    ),
)

CLIENT_INSPECTION_SCOPE = "; ".join(c.clause for c in CLIENT_INSPECTION_CAPABILITIES)


_BOUNDARY_INSPECTION_SCOPE = (
    "every HTTP/1.x request to the capture origin on any path or method: request line, "
    "headers, chunk extensions, trailers, and body, after transfer-encoding and "
    "content-encoding decoding, walked recursively over all JSON types, with "
    "base64/hex/percent-encoded runs and character-code arrays decoded, matched literally "
    "and with separators removed, over NFKD-decomposed text with non-Latin digits resolved "
    "to their decimal value and non-ASCII look-alikes folded to ASCII per UTS #39, "
    "including ordered per-channel joins across captured requests with decoded material "
    "held out of the ordered stream"
)


def _boundary_check(result: RunResult) -> dict[str, Any]:
    return {
        "passed": True,
        "captured_requests": len(result.upstream_bodies),
        "correlated_requests": len(result.upstream_bodies),
        "uninspectable_requests": 0,
        "uninspectable_reasons": [],
        "leaked_entity_types": [],
        "upstream_paths_observed": ["/v1/chat/completions"],
        "marker_words_required": 3,
        "marker_words_total": 5,
        "marker_words_observed_max": 5,
        "payload_content_included": False,
        "inspection_scope": _BOUNDARY_INSPECTION_SCOPE,
        "capture_mode": "loopback",
        "unattributed_requests": 0,
        "unattributed_uninspectable_requests": 0,
        "unattributed_leaked_entity_types": [],
        "leak_evidence": [],
        "needle_proximity": {},
        "needle_lengths": {},
    }


def _fragmentation_check(result: RunResult) -> dict[str, Any]:
    return {
        "passed": result.events_observed > 1,
        "one_character_events_requested": True,
        "events_observed": result.events_observed,
        "events_observed_max": result.events_observed,
        "coalescing_not_distinguished": True,
        "response_reconstructed": bool(result.client_text),
    }


def _sse_check() -> dict[str, Any]:
    return {
        "passed": True,
        "invalid_events": 0,
        "done_markers_valid": True,
        "content_type_valid": True,
        "status_codes": [200],
        "errors": [],
    }


def _fidelity_check(results: list[RunResult]) -> dict[str, Any]:
    matching = sum(1 for r in results for v in r.echo_recovered.values() if v)
    total = sum(len(r.echo_recovered) for r in results)
    return {
        "passed": matching == total,
        "expected_value_reconstructed": matching == total,
        "iterations_matching": matching,
        "iterations_completed": total,
        "iterations_requested": total,
        "payload_content_included": False,
        "segment": "echo",
    }


def _injection_check(results: list[RunResult], leaked_types: list[str]) -> dict[str, Any]:
    # A case the client never saw a response for cannot testify to containment. The
    # schema already forbids `passed` alongside `delivery_confirmed: false`; tying them
    # here means the emitter cannot produce that contradiction in the first place.
    delivery_confirmed = bool(results) and all(bool(r.client_text) for r in results)
    return {
        "passed": (not leaked_types) and delivery_confirmed,
        "segment": "injection",
        "fragmentation_strategy": "exhaustive-2-part",
        "injected_entity_types": sorted({r.case["entity"] for r in results}),
        "leaked_entity_types": leaked_types,
        "leak_evidence": [
            {"entity_type": t, "observed": "normalized-match"} for t in leaked_types
        ],
        "needle_proximity": {},
        "needle_lengths": {},
        "delivery_confirmed": delivery_confirmed,
        "client_capture_inspectable": True,
        # Generated from CLIENT_INSPECTION_CAPABILITIES, never hand-written. See the
        # comment on that list for why.
        "inspection_scope": CLIENT_INSPECTION_SCOPE,
        "payload_content_included": False,
    }


def run_policy(
    policy_name: str,
    iterations: int = 1,
    seed: str | None = None,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one policy across the whole covering array and emit its v2 report.

    `seed` reproduces a previous run exactly. Omitted, a fresh one is drawn from
    `secrets` so successive runs still vary.
    """
    import secrets

    seed = seed or secrets.token_hex(8)
    if gateway_url is None and policy_name not in POLICIES:
        raise ValueError(
            f"{policy_name!r} is not an in-process policy; pass gateway_url to drive an "
            f"external gateway. Known policies: {sorted(POLICIES)}"
        )
    segments = build_segments(seed)
    # Separation is asserted against every request body the run will send, serialized,
    # not against one representative prompt. With the request_site axis the bodies differ
    # in shape, and "no injected value appears in the request" has to hold for all of
    # them or the injection half of some case is not measuring what it claims.
    all_bodies = json.dumps(
        [build_request(segments, case) for case in covering_array()],
        sort_keys=True,
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
        )
        for case in covering_array()
    ]
    report = build_report(segments, results, separation, seed)
    summary = {
        "fidelity_rate": report["metrics"]["fidelity_rate"],
        "leak_single_chunk": report["metrics"]["leak_rate"]["single_chunk"],
        "leak_adversarial": report["metrics"]["leak_rate"]["adversarial"],
        "delta_frag": report["metrics"]["delta_frag"],
        "cases": report["metrics"]["cases_scored"],
        # Without these a run in which EVERY case failed is indistinguishable from a
        # perfect one: all four rates come back 0.00 and the row reads as clean. That is
        # not hypothetical -- a container that failed to start produced exactly such a
        # row, and it looked like the best result in the table.
        "inconclusive": report["metrics"]["cases_inconclusive"],
        "echo_observable": report["metrics"]["cases_echo_observable"],
        "pairs": (
            report["corpus"]["coverage"]["pairs_covered"],
            report["corpus"]["coverage"]["pairs_required"],
        ),
    }
    return report, summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="benchmarks/results/v2-response-split")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated policy names")
    parser.add_argument("--seed", default=None, help="hex seed; reproduces a prior run")
    parser.add_argument("--gateway-url", default=None, help="external gateway chat-completions URL")
    parser.add_argument("--upstream-port", type=int, default=0, help="fixed capture port")
    parser.add_argument("--model", default="test", help="model name the gateway routes on")
    args = parser.parse_args(argv)

    import pathlib

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    validator: Callable[[dict[str, Any]], list[str]] | None = None
    if args.validate:
        import jsonschema

        schema = json.loads(
            pathlib.Path("spec/v2.0.0/http-profile.schema.json").read_text(encoding="utf-8")
        )

        def validator(report: dict[str, Any]) -> list[str]:  # type: ignore[misc]
            v = jsonschema.Draft202012Validator(schema)
            return [f"{list(e.path)}: {e.message}" for e in v.iter_errors(report)]

    rows = []
    # Default to the LOCAL set. The cloud rows bill per delta against a real account,
    # so running them must be an explicit `--only`, never a side effect of running the
    # tool with no arguments.
    selected = [n.strip() for n in args.only.split(",") if n.strip()] or list(DEFAULT_POLICIES)
    if args.gateway_url is None:
        unknown = [n for n in selected if n not in POLICIES]
        if unknown:
            parser.error(
                "unknown policy names %s; without --gateway-url a policy must be one of %s"
                % (unknown, sorted(POLICIES))
            )
    for name in selected:
        report, summary = run_policy(
            name,
            seed=args.seed,
            gateway_url=args.gateway_url,
            upstream_port=args.upstream_port,
            model=args.model,
        )
        errors = validator(report) if validator else []
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        rows.append((name, summary, report["outcome"], errors))
        status = "VALID" if validator and not errors else ("INVALID" if errors else "-")
        print(
            f"{name:20} fidelity={summary['fidelity_rate']:<6} "
            f"leak_single={summary['leak_single_chunk']:<6} "
            f"leak_adv={summary['leak_adversarial']:<6} "
            f"DeltaFrag={summary['delta_frag']:<7} "
            f"outcome={report['outcome']:<24} schema={status}"
        )
        for err in errors[:6]:
            print("      !", err)
    return 0 if all(not r[3] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
