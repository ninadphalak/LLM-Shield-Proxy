"""Minimal v2.0.0 profile emitter — demonstrates the echo/injection response split.
Drives two loopback HTTP servers (upstream and gateway) with a streaming client to measure
FidelityRate (rehydration of echoed placeholders) and LeakRate (redaction of new injected values).
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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx

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
# Label for report generations. Bump when measurement-affecting behavior changes.
EMITTER_VERSION = "0.2.1"

# Simple injection detector; deliberately blind to specific fixture values.
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARDPAN", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Ordered after SSN (3-2-4 vs 3-3-4) to prefer the more specific shape.
    ("USPHONE", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
)

_PLACEHOLDER = re.compile(r"\[(?:EMAIL|SSN|CARDPAN)_\d+\]")


# --------------------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segments:
    """Disjoint value sets: one echoed, one injected."""

    echo: dict[str, str]
    injection: dict[str, str]


# Map v1 entity IDs to v2's 10-char cap to bound vault retention costs.
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

    Unlike v1, v2 needs seeded reproducibility for the corpus block. Imports v1's spaces/rules.
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

    # USPHONE uses fictitious ranges to balance publish safety and detector visibility.
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

    Same seed produces same values. Disjointness is enforced via _normalized_disjoint.
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

    Prevents overlapping values where echo rehydration mimics an injection leak.
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
    """Redact raw values first, then rehydrate placeholders to avoid double-redaction."""
    out = text
    for _entity, pattern in _DETECTORS:
        out = pattern.sub("[REDACTED]", out)
    for token, original in vault.items():
        out = out.replace(token, original)
    return out


class Passthrough(Policy):
    """Forwards bytes untouched."""

    name = "passthrough"

    def feed(self, delta: str) -> str:
        return delta


class RedactAll(Policy):
    """One-way anonymizer: redacts detected values, never rehydrates."""

    name = "redact-all"
    redacts = True

    def feed(self, delta: str) -> str:
        out = delta
        for _entity, pattern in _DETECTORS:
            out = pattern.sub("[REDACTED]", out)
        return out


class ChunkLocal(Policy):
    """Rehydrates and redacts chunk-by-chunk statelessly. Vulnerable to fragmentation."""

    name = "chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        return _redact_then_rehydrate(delta, self.vault)


class Retaining(Policy):
    """Rehydrates and redacts with a bounded suffix carry to handle fragmentation safely."""

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
        """Cut prefix at the last whitespace before the tail to prevent partial emission."""
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
    """Bounded retention combined with decoding to handle percent-encoded injection."""

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
                # Redact the value across surface encodings.
                for surface in (original, quote(original, safe=""), quote(original)):
                    out = out.replace(surface, "[REDACTED]")
        return out

    def feed(self, delta: str) -> str:
        return self._detect_over_decodings(super().feed(delta))

    def flush(self) -> str:
        return self._detect_over_decodings(super().flush())


PRESIDIO_ANALYZER_URL = "http://127.0.0.1:5002/analyze"


def _presidio_redact(text: str, url: str = PRESIDIO_ANALYZER_URL) -> str:
    """Redact via Presidio HTTP analyzer, applying spans right-to-left."""
    if not text.strip():
        return text
    payload = json.dumps({"text": text, "language": "en"}).encode()
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310  # nosec B310 - fetching the operator-supplied target URL is this harness's purpose
        spans = json.loads(response.read().decode("utf-8"))
    keep = [s for s in spans if s.get("entity_type") != "URL"]
    out = text
    for span in sorted(keep, key=lambda s: s["start"], reverse=True):
        out = out[: span["start"]] + "[REDACTED]" + out[span["end"] :]
    return out


class PresidioChunkLocal(Policy):
    """Stateless Presidio integration applied per delta. Exposes fragmentation leaks."""

    name = "presidio-chunk-local"
    rehydrates = True
    redacts = True

    def feed(self, delta: str) -> str:
        out = _presidio_redact(delta)
        for token, original in self.vault.items():
            out = out.replace(token, original)
        return out


class PresidioRetaining(Retaining):
    """Presidio integration behind a bounded suffix carry to fix fragmentation."""

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
# Evaluates commercial SaaS detectors (DLP/Model Armor) under real billing accounts.
# Credentials via `gcloud auth application-default login` requiring `x-goog-user-project`.
# Network timeouts are distinct from local sockets to accommodate cold starts.
# --------------------------------------------------------------------------------------
GCP_CONNECT_TIMEOUT = float(os.environ.get("V2_GCP_CONNECT_TIMEOUT", "5"))
GCP_READ_TIMEOUT = float(os.environ.get("V2_GCP_READ_TIMEOUT", "60"))

# Retry bounds for cloud endpoints to gracefully handle rate limiting (429) or 503s.
GCP_MAX_ATTEMPTS = int(os.environ.get("V2_GCP_MAX_ATTEMPTS", "6"))
GCP_BACKOFF_BASE = float(os.environ.get("V2_GCP_BACKOFF_BASE", "1.0"))
GCP_BACKOFF_CAP = float(os.environ.get("V2_GCP_BACKOFF_CAP", "60"))
GCP_RETRY_STATUS = frozenset({429, 503})

# Refresh `gcloud` access tokens before their 1-hour expiration.
GCP_TOKEN_TTL_SECONDS = float(os.environ.get("V2_GCP_TOKEN_TTL_SECONDS", "3000"))

# Jitter generator isolated from the corpus PRNG to preserve reproducibility.
_GCP_JITTER = random.Random()

_GCP_TOKEN_CACHE: dict[str, Any] = {}
_GCP_TOKEN_LOCK = threading.Lock()


def _gcloud(argv: list[str], what: str) -> str:
    # Use `shutil.which` instead of `shell=True` to resolve `gcloud` safely on Windows/POSIX.
    import shutil
    import subprocess  # nosec B404 - a fixed argv to the operator's own gcloud, no shell

    executable = shutil.which(argv[0])
    if executable is None:
        raise RuntimeError(f"gcloud {what} unavailable: {argv[0]!r} is not on PATH")

    done = subprocess.run(  # nosec B603 - resolved absolute path, fixed argv, shell=False
        [executable, *argv[1:]],
        capture_output=True,
        text=True,
        check=False,
    )
    value = done.stdout.strip()
    if done.returncode != 0 or not value:
        raise RuntimeError(f"gcloud {what} unavailable: {done.stderr.strip()[:200]}")
    return value


def _gcp_context() -> tuple[str, str]:
    """Get access token and project ID, refreshing the token before expiration."""
    with _GCP_TOKEN_LOCK:
        if "project" not in _GCP_TOKEN_CACHE:
            _GCP_TOKEN_CACHE["project"] = _gcloud(
                ["gcloud", "config", "get-value", "project"], "project"
            )
        issued = _GCP_TOKEN_CACHE.get("issued_at")
        if issued is None or (time.monotonic() - issued) >= GCP_TOKEN_TTL_SECONDS:
            _GCP_TOKEN_CACHE["token"] = _gcloud(
                ["gcloud", "auth", "print-access-token"], "token"
            )
            _GCP_TOKEN_CACHE["issued_at"] = time.monotonic()
        return _GCP_TOKEN_CACHE["token"], _GCP_TOKEN_CACHE["project"]


_GCP_OPENERS: dict[tuple[float, float], Any] = {}


def _gcp_opener(connect_timeout: float, read_timeout: float) -> Any:
    """An opener that separates connect and read timeouts. Cached per timeout pair."""
    key = (connect_timeout, read_timeout)
    cached = _GCP_OPENERS.get(key)
    if cached is not None:
        return cached

    import http.client
    import urllib.request

    class _PhasedHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # `do_open` passes `timeout=req.timeout`; the connect deadline wins here.
            kwargs["timeout"] = connect_timeout
            super().__init__(*args, **kwargs)

        def connect(self) -> None:
            super().connect()
            # Re-arm the socket with the read deadline after connection.
            if self.sock is not None:
                self.sock.settimeout(read_timeout)

    class _PhasedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: Any) -> Any:
            return self.do_open(_PhasedHTTPSConnection, req, context=self._context)

    opener = urllib.request.build_opener(_PhasedHTTPSHandler)
    _GCP_OPENERS[key] = opener
    return opener


def _gcp_retry_delay(error: HTTPError, attempt: int) -> float:
    """Determine retry delay, respecting `Retry-After` headers or falling back to jittered exponential."""
    import datetime

    header = ((error.headers.get("Retry-After") if error.headers else None) or "").strip()
    if header:
        try:
            return max(0.0, float(int(header)))
        except ValueError:
            from email.utils import parsedate_to_datetime

            try:
                when = parsedate_to_datetime(header)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=datetime.timezone.utc)
                now = datetime.datetime.now(datetime.timezone.utc)
                return max(0.0, (when - now).total_seconds())
    # Exponential backoff with jitter to prevent thunder-herd quota collisions.
    return _GCP_JITTER.uniform(0.0, GCP_BACKOFF_BASE * (2 ** (attempt - 1)))


def _gcp_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to Google API with bounded retries and fresh tokens per attempt."""
    data = json.dumps(payload).encode()
    for attempt in range(1, GCP_MAX_ATTEMPTS + 1):
        token, project = _gcp_context()
        request = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-goog-user-project": project,
            },
        )
        try:
            opener = _gcp_opener(GCP_CONNECT_TIMEOUT, GCP_READ_TIMEOUT)
            with opener.open(request) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code not in GCP_RETRY_STATUS or attempt == GCP_MAX_ATTEMPTS:
                # Raise non-retryable errors immediately.
                raise
            wait = min(GCP_BACKOFF_CAP, _gcp_retry_delay(error, attempt))
            print(
                f"    gcp {error.code} on {url.rsplit('/', 1)[-1]}; "
                f"retry {attempt}/{GCP_MAX_ATTEMPTS - 1} in {wait:.1f}s",
                flush=True,
            )
            time.sleep(wait)
    # Unreachable: the final attempt either returns or re-raises.
    raise RuntimeError(f"gcp request to {url} exhausted {GCP_MAX_ATTEMPTS} attempts")


def _dlp_redact(text: str) -> str:
    """Google Cloud DLP de-identification. DLP rewrites the string itself."""
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


def _all_pairs(
    axes: dict[str, tuple[str, ...]] | None = None,
    feasible: "Callable[[dict[str, str]], bool] | None" = None,
) -> set[tuple[str, str, str, str]]:
    """Pairs of axis values that SOME feasible case can carry, constrained by `feasible`."""
    import itertools

    axes = AXES if axes is None else axes
    names = list(axes)
    pairs: set[tuple[str, str, str, str]] = set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for va in axes[a]:
                for vb in axes[b]:
                    pairs.add((a, va, b, vb))
    if feasible is None:
        return pairs
    reachable: set[tuple[str, str, str, str]] = set()
    for combo in itertools.product(*axes.values()):
        case = dict(zip(names, combo))
        if feasible(case):
            reachable |= _pairs_of(case, axes)
    return pairs & reachable


def covering_array(
    axes: dict[str, tuple[str, ...]] | None = None,
    feasible: "Callable[[dict[str, str]], bool] | None" = None,
) -> list[dict[str, str]]:
    """Greedy pairwise covering array over profile axes, plus fragmentation twins."""
    import itertools

    axes = AXES if axes is None else axes
    names = list(axes)
    candidates = [dict(zip(names, combo)) for combo in itertools.product(*axes.values())]
    if feasible is not None:
        candidates = [c for c in candidates if feasible(c)]
    remaining = _all_pairs(axes, feasible)
    chosen: list[dict[str, str]] = []
    while remaining:
        best, best_gain = None, -1
        for case in candidates:
            gain = len(remaining & _pairs_of(case, axes))
            if gain > best_gain:
                best, best_gain = case, gain
        if best is None or best_gain <= 0:
            break
        chosen.append(best)
        remaining -= _pairs_of(best, axes)
        candidates.remove(best)

    # Add fragmentation twins so DeltaFrag compares identical case conditions.
    seen = {tuple(sorted(c.items())) for c in chosen}
    for case in list(chosen):
        for value in axes["fragmentation"]:
            twin = dict(case, fragmentation=value)
            key = tuple(sorted(twin.items()))
            if key not in seen and (feasible is None or feasible(twin)):
                seen.add(key)
                chosen.append(twin)
    return chosen


def _pairs_of(
    case: dict[str, str], axes: dict[str, tuple[str, ...]] | None = None
) -> set[tuple[str, str, str, str]]:
    names = list(AXES if axes is None else axes)
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


def _sse_frames(events: Iterable[dict[str, Any]]) -> list[bytes]:
    """Split into one frame per event to allow writing and counting individually."""
    frames: list[bytes] = []
    for event in events:
        delta: dict[str, Any] = {"content": event.get("content", "")}
        for key, value in event.items():
            if key != "content":
                delta[key] = value
        frames.append(
            b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"
        )
    return frames


SSE_DONE_FRAME = b"data: [DONE]\n\n"


def _sse(events: Iterable[dict[str, Any]]) -> bytes:
    """Serialise events. `content` is delta text; others are sibling fields."""
    return b"".join(_sse_frames(events)) + SSE_DONE_FRAME


# Stated explicitly: `_make_upstream` reads no headers and rejects nothing.
CAPTURE_REQUIRES_AUTHENTICATION = False


# Strict, phased client deadlines to fail a stalled socket rather than hang a run.
CLIENT_CONNECT_TIMEOUT = float(os.environ.get("V2_CLIENT_CONNECT_TIMEOUT", "5"))
CLIENT_READ_TIMEOUT = float(os.environ.get("V2_CLIENT_READ_TIMEOUT", "10"))


def _client_timeout() -> httpx.Timeout:
    """The deadline every request this harness makes is subject to."""
    return httpx.Timeout(
        connect=CLIENT_CONNECT_TIMEOUT,
        read=CLIENT_READ_TIMEOUT,
        write=CLIENT_READ_TIMEOUT,
        pool=CLIENT_CONNECT_TIMEOUT,
    )


@dataclass
class UpstreamResponseRecord:
    """Empirical socket-write facts for one capture response."""

    data_events_written: int = 0
    completed: bool = False


@dataclass
class UpstreamState:
    segments: Segments
    case: dict[str, str]
    received_bodies: list[str] = field(default_factory=list)
    received_paths: list[str] = field(default_factory=list)
    response_records: list[UpstreamResponseRecord] = field(default_factory=list)
    response_records_lock: threading.Lock = field(default_factory=threading.Lock)
    # Where to cut the injected value for the attempt currently in flight.
    cuts: tuple[int, ...] = ()


# Partition families for fragmentation analysis.
PARTITION_FAMILIES: tuple[str, ...] = ("exhaustive-2-part", "exhaustive-3-part")
ORACLES: tuple[str, ...] = ("midpoint",) + PARTITION_FAMILIES + ("union-worst-case",)

# Cap on partition pieces to bound combinatorial explosion of 3-part splits.
DEFAULT_PARTITION_CAP = int(os.environ.get("V2_PARTITION_CAP", "6000"))


def _family_partitions(rendered: str, family: str) -> list[tuple[int, ...]]:
    """Every partition of `rendered` in one family, as tuples of internal cut offsets."""
    n = len(rendered)
    if family == "exhaustive-2-part":
        return [(i,) for i in range(1, n)]
    if family == "exhaustive-3-part":
        # Every pair of distinct internal cuts, i < j, both in 1..N-1.
        return [(i, j) for i in range(1, n - 1) for j in range(i + 1, n)]
    raise ValueError(f"unknown partition family {family!r}")


def injection_partitions(
    segments: Segments,
    case: dict[str, str],
    oracle: str = "midpoint",
    cap: int = DEFAULT_PARTITION_CAP,
) -> tuple[list[tuple[int, ...]], list[str], dict[str, int], dict[str, bool]]:
    """The partitions to try for one case, plus per-family counts and cap hits."""
    if oracle not in ORACLES:
        raise ValueError(f"unknown oracle {oracle!r}; known: {ORACLES}")
    if case["fragmentation"] == "single_chunk":
        return [()], [], {}, {}
    rendered = _encode(segments.injection[case["entity"]], case["encoding"])
    if oracle == "midpoint":
        return (
            [(len(rendered) // 2,)],
            ["midpoint"],
            {"midpoint": 1},
            {"midpoint": False},
        )

    wanted = PARTITION_FAMILIES if oracle == "union-worst-case" else (oracle,)
    partitions: list[tuple[int, ...]] = []
    families: list[str] = []
    attempted: dict[str, int] = {}
    capped: dict[str, bool] = {}
    for family in wanted:
        enumerated = _family_partitions(rendered, family)
        if len(enumerated) > cap:
            attempted[family] = 0
            capped[family] = True
            continue
        attempted[family] = len(enumerated)
        capped[family] = False
        partitions.extend(enumerated)
        families.extend([family] * len(enumerated))
    return partitions, families, attempted, capped


def _partition_pieces(rendered: str, cuts: tuple[int, ...]) -> list[str]:
    """Cut `rendered` at internal offsets, ensuring concatenation recovers the original."""
    if not cuts:
        return [rendered]
    bounds = (0,) + cuts + (len(rendered),)
    pieces = [rendered[a:b] for a, b in zip(bounds, bounds[1:])]
    if "".join(pieces) != rendered:
        raise RuntimeError(f"partition {cuts!r} does not reconstruct the value")
    if any(not piece for piece in pieces):
        raise RuntimeError(f"partition {cuts!r} produced an empty piece")
    if any(rendered in piece for piece in pieces):
        raise RuntimeError(f"partition {cuts!r} left the complete value in one piece")
    return pieces


def _injection_events(
    segments: Segments, case: dict[str, str], cuts: tuple[int, ...] = ()
) -> list[dict[str, Any]]:
    """Build the injection segment for one case: one entity, encoded, carried, split."""
    raw = segments.injection[case["entity"]]
    rendered = _encode(raw, case["encoding"])
    pieces = _partition_pieces(rendered, cuts)
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
                # Answer 500 on harness bugs instead of silently disconnecting.
                message = json.dumps({"harness_error": f"{type(exc).__name__}: {exc}"}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(message)
                self.close_connection = True

        def _respond(self) -> None:
            # Record the response attempt before parsing the request.
            record = UpstreamResponseRecord()
            with state.response_records_lock:
                state.response_records.append(record)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            state.received_bodies.append(raw)
            state.received_paths.append(self.path)
            # Echo back whatever arrived at the case's specific request site.
            echoed = extract_site(json.loads(raw), state.case["request_site"])
            prompt = "" if echoed is None else echoed

            events = [{"content": f"You sent: {prompt}\n"}]
            events.extend(_injection_events(state.segments, state.case, state.cuts))
            frames = _sse_frames(events)
            body = b"".join(frames) + SSE_DONE_FRAME
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            # Refuse keep-alive so pooled gateway connections don't cross case fixtures.
            self.send_header("Connection", "close")
            self.end_headers()
            # Track frames written cleanly even if the connection resets mid-stream.
            for frame in frames:
                self.wfile.write(frame)
                with state.response_records_lock:
                    record.data_events_written += 1
            self.wfile.write(SSE_DONE_FRAME)
            # Flush buffered writes before FIN.
            self.wfile.flush()
            with state.response_records_lock:
                record.completed = True
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
            # Walk the whole body to expose site-specific fidelity defects.
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
            # `upstream_url` is the loopback capture.
            with urlopen(request, timeout=CLIENT_READ_TIMEOUT) as response:  # nosec B310 # noqa: S310
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
                # Sibling fields pass through the same policy.
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
            # Flush and close -- one exchange per connection.
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    return Handler


@dataclass
class RunResult:
    policy: str
    case: dict[str, str]
    client_text: str
    echo_recovered: dict[str, bool]
    # False if gateway dropped request site. Excludes case from FidelityRate.
    echo_observable: bool
    # Reason for transport/HTTP failure. Inconclusive, never counts as a non-leak.
    transport_error: str | None
    injection_leaked: bool
    events_observed: int
    upstream_bodies: list[str]
    latency_ms: list[float]
    # Empirical facts.
    status_codes: list[int] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)
    invalid_events: int = 0
    done_marker: bool = False
    # Tier of leak detection if present.
    leak_tier: str | None = None
    # Largest event count across splits.
    events_observed_max: int = 0
    # Events with data (excluding `[DONE]`).
    data_events_observed: int = 0
    # Capture written data frames.
    upstream_data_events: int = 0
    # Capture responses during the request.
    upstream_responses_observed: int = 0
    # Every request-target the capture was actually asked for, for this case.
    upstream_paths: list[str] = field(default_factory=list)
    # Internal partitions tried/leaked. Single chunk is 1 try.
    split_points_tried: int = 1
    split_points_leaked: int = 0
    # Oracle and family breakdown.
    oracle: str = "midpoint"
    partitions_attempted: dict[str, int] = field(default_factory=dict)
    partitions_leaked: dict[str, int] = field(default_factory=dict)
    # Whether a family was capped.
    partitions_capped: dict[str, bool] = field(default_factory=dict)
    partition_cap: int = DEFAULT_PARTITION_CAP
    partition_seconds: float = 0.0
    self_probe_ms: float = 0.0
    self_probe_url: str = ""


def _serve(
    handler: type[BaseHTTPRequestHandler], port: int = 0
) -> tuple[ThreadingHTTPServer, str]:
    # Allow port reuse only on POSIX to avoid silent port stealing on Windows.
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1/chat/completions"


def _stop(server: ThreadingHTTPServer) -> None:
    """Stop accepting AND release the listening socket."""
    server.shutdown()
    server.server_close()


@dataclass(frozen=True)
class ParsedStream:
    """One response body, parsed the way a conformant SSE client parses it."""

    events: list[str]
    residue: list[str]
    unknown_fields: list[str]
    comments: int
    undispatched_tail: str
    looked_like_sse: bool


_SSE_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


def _parse_sse(body: str) -> ParsedStream:
    """Conformant WHATWG HTML 9.2.6 event stream parsing.
    Prevents false passes from trailing spaces or multi-line events.
    """
    events: list[str] = []
    residue: list[str] = []
    unknown: list[str] = []
    comments = 0
    buffer: list[str] = []
    saw_field = False

    def dispatch() -> None:
        # A blank line with an empty buffer fires nothing.
        if buffer:
            events.append("\n".join(buffer))
        buffer.clear()

    for line in _SSE_LINE_SPLIT.split(body):
        if line == "":
            dispatch()
            continue
        if line.startswith(":"):
            comments += 1
            residue.append(line[1:])
            continue
        name, separator, value = line.partition(":")
        if not separator:
            name, value = line, ""
        elif value.startswith(" "):
            # Exactly ONE leading space is removed. A second space is data.
            value = value[1:]
        if name == "data":
            saw_field = True
            buffer.append(value)
        elif name in ("event", "id", "retry"):
            saw_field = True
            residue.append(value)
        else:
            # Treat unknown fields or plain JSON as residue.
            unknown.append(name)
            residue.append(line)

    tail = "\n".join(buffer)
    if tail:
        residue.append(tail)
    return ParsedStream(
        events=events,
        residue=residue,
        unknown_fields=sorted(set(unknown)),
        comments=comments,
        undispatched_tail=tail,
        looked_like_sse=saw_field,
    )


# The channel representing the client's visible text.
CONTENT_CHANNEL = ".choices[].delta.content"


def _ordered_channels(node: Any, flat: list[tuple[str, str]], path: str = "") -> None:
    """Every string in one event tagged with JSON path. Ensures cross-event reassembly."""
    if isinstance(node, dict):
        for key, value in node.items():
            _ordered_channels(value, flat, f"{path}.{key}")
    elif isinstance(node, list):
        for value in node:
            _ordered_channels(value, flat, f"{path}[]")
    elif isinstance(node, str):
        flat.append((path, node))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        # A digit run survives as a JSON number, and so does half of one.
        flat.append((path, repr(node)))


def _haystack_groups(sse: str) -> dict[str, list[str]]:
    """Groups of strings a client could see, categorized by reconstruction fidelity."""
    from pii_leak_benchmark.http_profile import _Inspection, _collect  # noqa: I001

    parsed = _parse_sse(sse)
    flat: list[tuple[str, str]] = []
    found = _Inspection()
    for payload in parsed.events:
        if payload == "[DONE]":
            continue
        shadowed = False

        def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            # `json.loads` keeps the LAST of duplicate keys and silently discards the
            # rest, so a value carried in a shadowed key reached the client and entered
            # NEITHER `events` (it is gone from the parsed object) NOR `residue` (the
            # parse succeeded). Demonstrated: `{"content":"<email>","content":"[REDACTED]"}`
            # scored as no leak. RFC 8259 permits duplicate names and says nothing about
            # which wins, so this is a shape a client may legitimately receive.
            nonlocal shadowed
            seen: set[str] = set()
            for key, _value in items:
                if key in seen:
                    shadowed = True
                    break
                seen.add(key)
            return dict(items)

        try:
            event = json.loads(payload, object_pairs_hook=_pairs)
        except json.JSONDecodeError:
            # Not JSON, but it still reached the client. Scanning the raw text is the
            # only safe answer; skipping it would be a false pass.
            _collect(payload, found)
            continue
        _ordered_channels(event, flat)
        _collect(event, found)
        if shadowed:
            # The parsed object is lossy for this event. Scan the bytes as well, the
            # same answer already given to an event that did not parse at all.
            _collect(payload, found)
    for text in parsed.residue:
        _collect(text, found)

    channels: dict[str, list[str]] = {}
    for path, value in flat:
        channels.setdefault(path, []).append(value)

    # RENDERABLE-TEXT SUBTREES. `content` is a string in the chat-completions shape and a
    # list of `{"type": ..., "text": ...}` parts in the multimodal one, and a stream may
    # use both. A value split with one half in the string form and the other in a part
    # was in no channel: the paths differ, and the whole-document join splices the part's
    # `type` discriminator between the halves.
    #
    # This is NOT the skip-list that was removed. That one EXCLUDED any key called
    # `content` or `text` from a stream, so a value could fall out of every haystack.
    # This is strictly ADDITIVE -- an extra channel that can only turn a miss into a
    # find -- and it is keyed on the two names the wire formats define as renderable
    # rather than on where the harness happens to put its own fixture.
    renderable: dict[str, list[str]] = {}
    for path, value in flat:
        segments = path.split(".")
        for index, segment in enumerate(segments):
            if segment.rstrip("[]") not in ("content", "text"):
                continue
            rest = segments[index + 1:]
            if all(s.rstrip("[]") in ("content", "text", "") for s in rest):
                # `content` and `content[]` are the SAME subtree -- the string form and
                # the list-of-parts form of one field -- so the key drops the brackets.
                key = ".".join([*segments[:index], segment.rstrip("[]")])
                renderable.setdefault(key, []).append(value)
            break

    siblings: list[str] = list(found.strings) + list(found.decoded_strings)
    from urllib.parse import unquote

    def _with_unquoted(values: list[str]) -> list[str]:
        return values + [unquote(v) for v in values]

    # ONE source of truth for both "did it leak" and "how was it recovered". Building
    # the tiers separately from the detection would let the two disagree, which is the
    # class of defect this module keeps finding.
    return {
        # groups[0][0] IS `client_text`. Its meaning must not drift: `delivery_confirmed`
        # and every published row depend on it.
        "same-path-join": _with_unquoted(
            ["".join(value for path, value in flat if path == CONTENT_CHANNEL)]
            + ["".join(values) for path, values in channels.items() if path != CONTENT_CHANNEL]
        ),
        # Joins that concatenate text from DIFFERENT paths with no separator. They close
        # real blind spots and they are also the only place this inspector can
        # manufacture a value nothing sent, so a match found only here is reported as the
        # weaker claim rather than as the same fact.
        #
        # The whole-document join is the one added after `content` + `raw.text`,
        # `content` + `tool_calls[].function.arguments` and a `content` string followed
        # by a `content` list-of-parts were all measured as MISSES while the value
        # reached the client whole.
        "cross-field-join": _with_unquoted(
            [
                "".join(value for path, value in flat if path != CONTENT_CHANNEL),
                "".join(value for _, value in flat),
                "".join(siblings),
                *("".join(values) for values in renderable.values()),
            ]
        ),
        # Individual strings as they arrived, plus everything `_collect` decoded and
        # every byte the SSE parser did not dispatch.
        "single-field": _with_unquoted(siblings),
    }


# Order is weakest-claim-last, and `_leak_tier` returns the first group that matches.
_HAYSTACK_TIERS = ("single-field", "same-path-join", "cross-field-join")

# Strongest first. A reader should discount `cross-field-join` before anything else: it
# is the only tier a coincidence can reach.
_LEAK_TIER_RANK = {
    "literal": 0,
    "single-field": 1,
    "same-path-join": 2,
    "cross-field-join": 3,
}


def _haystacks(sse: str) -> list[str]:
    """Flattened `_haystack_groups`, `client_text` first. The detection surface."""
    groups = _haystack_groups(sse)
    return [*groups["same-path-join"], *groups["cross-field-join"], *groups["single-field"]]


def _leak_tier(value: str, sse: str) -> str | None:
    """HOW a value was recovered from the response, or None if it was not.

    The boundary check has said this since it stopped being a literal -- "a bare entity
    name is an accusation with no way for the accused to check it" -- and the response
    check did not: every entry in `leak_evidence` read `observed: normalized-match`
    whether the value arrived verbatim in one event or was assembled by concatenating two
    unrelated fields. Constructed and measured, all three found:

        trace_id "4242-4242-" + span_id "4242-4242"  -> a test PAN
        id "chatcmpl-590-"    + model "555-0126"     -> a USPHONE
        author "ozvqklmr@"    + host "example.com"   -> an EMAIL

    Those are real recoveries of a value the client could reconstruct, so suppressing
    them would be a false pass. Ranking them is the answer, not dropping them.
    """
    if value and value in sse:
        return "literal"
    groups = _haystack_groups(sse)
    for tier in _HAYSTACK_TIERS:
        if _present(value, groups[tier]):
            return tier
    return None


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


def _self_probe(url: str, state: UpstreamState) -> float:
    """Confirm the server answering the capture URL is THIS run's, before any target traffic.

    `capture.self_probe` was reported as `performed: true, recorded: true,
    round_trip_ms: 0.0` in every report ever emitted, and no probe was ever sent. The
    schema's own description says what it is for -- "the fail-closed check that the capture
    was reachable and recording at the address the target was configured with; a run that
    could not confirm it aborts and produces no report" -- which is why both fields are
    `const true` there. A constant satisfying its own schema is the failure this profile
    keeps finding in other people's software.

    It is not redundant with the "capture recorded no request" guard in `run_case`. That
    one fires after the target has answered, and cannot distinguish "the gateway never
    called upstream" from "something else owns this port". This runs first, with a nonce
    only this process knows, so a stale capture holding the port fails here instead of
    silently scoring the run -- the Windows SO_REUSEADDR case that produced a perfect row
    from a measurement that never happened.
    """
    import secrets as _secrets

    nonce = _secrets.token_hex(8)
    started = time.perf_counter()
    with httpx.Client(timeout=_client_timeout(), trust_env=False) as client:
        client.post(
            url,
            content=json.dumps({"__probe__": nonce}).encode(),
            headers={"Content-Type": "application/json", "Connection": "close"},
        ).read()
    elapsed = (time.perf_counter() - started) * 1000.0
    seen = any(nonce in body for body in state.received_bodies)
    state.received_bodies.clear()
    # The probe is a response the capture wrote, and it is not one of the case's. Remove
    # its response record before target traffic starts.
    with state.response_records_lock:
        state.response_records.clear()
    if not seen:
        raise RuntimeError(
            f"capture self-probe failed: {url} answered but this run's capture did not "
            "record the probe, so another server owns that address. Refusing to measure."
        )
    return elapsed


def run_case(
    segments: Segments,
    policy_name: str,
    case: dict[str, str],
    iterations: int = 3,
    gateway_url: str | None = None,
    upstream_port: int = 0,
    model: str = "test",
    oracle: str = "midpoint",
    partition_cap: int = DEFAULT_PARTITION_CAP,
) -> RunResult:
    """Drive one corpus case end to end over loopback HTTP.

    `gateway_url` points the profile at an EXTERNAL gateway -- a real proxy already
    running and already configured to use this harness's capture as its upstream. In that
    mode `policy_name` is only a label for the report; no in-process policy runs, and the
    masking the gateway does (or fails to do) is entirely its own.

    `oracle` selects which partition family or families an adversarial case is cut into,
    and the case leaks if ANY enumerated partition leaks. See `injection_partitions` for
    why the midpoint alone is a weak oracle and why the cap turns a family inconclusive
    rather than shortening it.

    Direct callers must use `iterations=1`. The historical multi-iteration path retains
    only the last response for leak inspection, so `run_policy` rejects larger values
    until the next evidence round can change this instrumented function and regenerate
    every affected report.
    """
    points, families, attempted, capped = injection_partitions(
        segments, case, oracle=oracle, cap=partition_cap
    )
    leaked_by_family: dict[str, int] = {f: 0 for f in attempted}
    state = UpstreamState(segments=segments, case=case)
    upstream, upstream_url = _serve(_make_upstream(state), port=upstream_port)
    probe_ms = 0.0
    try:
        probe_ms = _self_probe(upstream_url, state)
    except Exception:
        _stop(upstream)
        raise
    gateway = None
    if gateway_url is None:
        gateway, gateway_url = _serve(_make_gateway(upstream_url, policy_name))
    latencies: list[float] = []
    first_sse = ""
    leaked_points = 0
    # THESE ACCUMULATE ACROSS SPLIT POINTS. They used to be plain assignments inside the
    # loop, so they described only the LAST split point while `leaked_points` accumulated
    # over all of them -- which made `--exhaustive-splits`, the STRONGER oracle, report
    # LESS framing than the midpoint. Measured with a relay that ships one malformed
    # event on every split point but the last: midpoint reported `invalid_events: 32`,
    # exhaustive reported 16 for the identical traffic, and `events_observed_max` came
    # out 5 against the midpoint's 6.
    events_first = 0
    events_max = 0
    upstream_events_first = 0
    upstream_responses_first = 0
    invalid_events = 0
    done_marker = True
    done_marker_first = False
    leak_tier: str | None = None
    statuses: list[int] = []
    content_types: list[str] = []
    transport_error: str | None = None
    needle = segments.injection[case["entity"]]
    # The request does not vary with the split point -- only the capture's RESPONSE does.
    body = json.dumps(build_request(segments, case, model=model)).encode()
    # One client per case constructed outside the timed region to avoid timing overhead.
    client = httpx.Client(timeout=_client_timeout(), trust_env=False)
    try:
        partitions_started = time.perf_counter()
        for index, cuts in enumerate(points):
            state.cuts = cuts
            sse = ""
            for _ in range(iterations):
                # Correlate capture responses to this gateway request via before/after slice.
                with state.response_records_lock:
                    response_record_start = len(state.response_records)
                started = time.perf_counter()
                headers = {"Content-Type": "application/json"}
                token = os.environ.get("V2_GATEWAY_TOKEN")
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                headers.update(_extra_gateway_headers())
                # Force `Connection: close` so pooled connections don't reach previous fixtures.
                headers["Connection"] = "close"
                try:
                    response = client.post(gateway_url, content=body, headers=headers)
                    statuses.append(int(response.status_code))
                    if response.status_code >= 400:
                        # Raise explicit HTTPStatusError for 4xx/5xx to fail inconclusive.
                        raise httpx.HTTPStatusError(
                            f"{response.status_code} {response.reason_phrase}",
                            request=response.request,
                            response=response,
                        )
                    # Explicit decode instead of `.text` to ignore gateway Content-Type codec.
                    sse = response.content.decode("utf-8", "replace")
                    content_types.append(response.headers.get("Content-Type", "") or "")
                except Exception as exc:  # noqa: BLE001
                    # Gateway refused the case (or timeout). Score as inconclusive.
                    transport_error = f"{type(exc).__name__}: {exc}"
                    sse = ""
                with state.response_records_lock:
                    attempt_records = list(
                        state.response_records[response_record_start:]
                    )
                    attempt_upstream_events = sum(
                        record.data_events_written for record in attempt_records
                    )
                # Extract first-attempt totals.
                if index == 0:
                    upstream_events_first = attempt_upstream_events
                    upstream_responses_first = len(attempt_records)
                if transport_error is not None:
                    break
                latencies.append((time.perf_counter() - started) * 1000.0)
            if transport_error is not None:
                break
            parsed = _parse_sse(sse)
            observed = len(parsed.events)
            events_max = max(events_max, observed)
            done_marker = done_marker and "[DONE]" in parsed.events
            invalid_events += _count_invalid_events(parsed)
            if index == 0:
                first_sse = sse
                events_first = observed
                # Keep the [DONE] marker boolean paired with events_first.
                done_marker_first = "[DONE]" in parsed.events
            tier = _leak_tier(needle, sse)
            if tier is not None:
                leaked_points += 1
                if families:
                    leaked_by_family[families[index]] += 1
                # Keep the strongest leak tier seen across split points.
                if leak_tier is None or _LEAK_TIER_RANK[tier] < _LEAK_TIER_RANK[leak_tier]:
                    leak_tier = tier
    finally:
        client.close()
        _stop(upstream)
        if gateway is not None:
            _stop(gateway)

    # Fail case if the response did not hit this run's capture.
    if not state.received_bodies and transport_error is None:
        transport_error = (
            "capture recorded no request: the response did not come from this run's "
            "upstream, so nothing about the gateway was measured"
        )
    # Fidelity read from first attempt; leak verdict from all attempts.
    hay = _haystacks(first_sse)
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
        injection_leaked=leaked_points > 0,
        leak_tier=leak_tier,
        events_observed=events_first,
        events_observed_max=events_max,
        data_events_observed=max(0, events_first - (1 if done_marker_first else 0)),
        upstream_data_events=upstream_events_first,
        upstream_responses_observed=upstream_responses_first,
        upstream_bodies=list(state.received_bodies),
        upstream_paths=sorted(set(state.received_paths)),
        latency_ms=latencies,
        status_codes=statuses,
        content_types=content_types,
        invalid_events=invalid_events,
        done_marker=done_marker,
        split_points_tried=len(points),
        split_points_leaked=leaked_points,
        oracle=oracle,
        partitions_attempted=dict(attempted),
        partitions_leaked=dict(leaked_by_family),
        partitions_capped=dict(capped),
        partition_cap=partition_cap,
        partition_seconds=round(time.perf_counter() - partitions_started, 4),
        self_probe_ms=round(probe_ms, 4),
        self_probe_url=upstream_url,
    )


def _count_invalid_events(parsed: "ParsedStream") -> int:
    """Dispatched events whose payload is neither `[DONE]` nor parseable JSON."""
    bad = 0
    for payload in parsed.events:
        if payload == "[DONE]":
            continue
        try:
            json.loads(payload)
        except json.JSONDecodeError:
            bad += 1
    return bad


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def _rate(flags: Iterable[bool]) -> float:
    values = list(flags)
    return round(sum(1 for f in values if f) / len(values), 4) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    """Round-to-nearest order statistic."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[index], 4)


def _assert_derivations(
    results: list[RunResult],
    published: dict[str, Any],
    published_digest: str,
    axes: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    """Recompute published metrics from results and refuse to emit on mismatch."""
    scored = [r for r in results if r.transport_error is None]
    single = [r for r in scored if r.case["fragmentation"] == "single_chunk"]
    adversarial = [r for r in scored if r.case["fragmentation"] == "adversarial"]
    observable = [r for r in scored if r.echo_observable]

    expected = {
        "leak_rate.single_chunk": _rate(r.injection_leaked for r in single),
        "leak_rate.adversarial": _rate(r.injection_leaked for r in adversarial),
        "leak_rate.overall": _rate(r.injection_leaked for r in scored),
        "fidelity_rate": (
            _rate(v for r in observable for v in r.echo_recovered.values())
            if observable
            else 0.0
        ),
        "cases_scored": len(results),
        "cases_applicable": len(scored),
        "cases_inconclusive": len(results) - len(scored),
        "cases_echo_observable": len(observable),
    }
    expected["delta_frag"] = round(
        expected["leak_rate.adversarial"] - expected["leak_rate.single_chunk"], 4
    )

    def _get(path: str) -> Any:
        node: Any = published
        for key in path.split("."):
            node = node[key]
        return node

    for path, want in expected.items():
        got = _get(path)
        if got != want:
            raise RuntimeError(
                f"published {path}={got!r} does not follow from the {len(results)} run "
                f"results, which give {want!r}; refusing to emit a report whose numbers "
                "do not come from the measurement beside them"
            )

    # Rebuild the sidecar case records and digest independently.
    rebuilt = sorted(
        ({k: r.case[k] for k in sorted(AXES if axes is None else axes)} for r in results),
        key=lambda c: tuple(sorted(c.items())),
    )
    rebuilt_digest = hashlib.sha256(
        json.dumps(rebuilt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if rebuilt_digest != published_digest:
        raise RuntimeError(
            "cases_digest does not match a digest recomputed from the run results; the "
            "published case records are not the cases that were measured"
        )
    if len(rebuilt) != published["cases_scored"]:
        raise RuntimeError(
            f"cases_scored {published['cases_scored']} does not match the {len(rebuilt)} "
            "case records behind cases_digest"
        )
    return True


def _discordance(results: list[RunResult]) -> dict[str, Any]:
    """The paired 2x2 table behind DeltaFrag."""
    scored = {
        (_twin_key(r.case), r.case["fragmentation"]): r
        for r in results
        if r.transport_error is None
    }
    keys = sorted({k for k, arm in scored if (k, "single_chunk") in scored
                   and (k, "adversarial") in scored})
    both = adv_only = single_only = neither = 0
    for key in keys:
        s = scored[(key, "single_chunk")].injection_leaked
        a = scored[(key, "adversarial")].injection_leaked
        if s and a:
            both += 1
        elif a:
            adv_only += 1
        elif s:
            single_only += 1
        else:
            neither += 1
    pairs = len(keys)
    return {
        "pairs_complete": pairs,
        "pairs_incomplete": len(
            {k for k, _arm in scored}
        ) - pairs,
        "both_arms_leaked": both,
        "adversarial_only": adv_only,
        "single_chunk_only": single_only,
        "neither_arm_leaked": neither,
        # Recomputed marginal rate difference for manual verification.
        "delta_frag_from_discordance": (
            round((adv_only - single_only) / pairs, 4) if pairs else 0.0
        ),
    }


def _axis_arms(
    results: list[RunResult], axes: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, Any]]:
    """Per axis value, the two fragmentation arms separately and their DeltaFrag."""
    scored = [r for r in results if r.transport_error is None]
    out: dict[str, dict[str, Any]] = {}
    for axis in axes:
        if axis == "fragmentation":
            continue
        slice_out: dict[str, Any] = {}
        for value in axes[axis]:
            rows = [r for r in scored if r.case[axis] == value]
            if not rows:
                continue
            single = [r for r in rows if r.case["fragmentation"] == "single_chunk"]
            adv = [r for r in rows if r.case["fragmentation"] == "adversarial"]
            paired = len(
                {_twin_key(r.case) for r in single} & {_twin_key(r.case) for r in adv}
            )
            s_rate = _rate(r.injection_leaked for r in single)
            a_rate = _rate(r.injection_leaked for r in adv)
            slice_out[value] = {
                "single_chunk": {
                    "applicable": len(single),
                    "leaked": sum(1 for r in single if r.injection_leaked),
                    "leak_rate": s_rate,
                },
                "adversarial": {
                    "applicable": len(adv),
                    "leaked": sum(1 for r in adv if r.injection_leaked),
                    "leak_rate": a_rate,
                },
                "paired_cases": paired,
                "delta_frag": round(a_rate - s_rate, 4),
            }
        if slice_out:
            out[axis] = slice_out
    return out


def _twin_key(case: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """A case's identity with fragmentation removed: its pair partner's address."""
    return tuple(sorted((k, v) for k, v in case.items() if k != "fragmentation"))


def _partition_oracle_block(results: list[RunResult]) -> dict[str, Any]:
    """Summary of partitions enumerated, what leaked, and the union-based worst case."""
    scored = [r for r in results if r.transport_error is None]
    adversarial = [r for r in scored if r.case["fragmentation"] == "adversarial"]
    single = {_twin_key(r.case): r for r in scored if r.case["fragmentation"] == "single_chunk"}
    # Read the oracle from every result.
    oracles = {r.oracle for r in results} or {"midpoint"}
    oracle = oracles.pop() if len(oracles) == 1 else "mixed"
    caps = {r.partition_cap for r in results}

    def _arm(rows: list[RunResult], leaked: list[bool]) -> dict[str, Any]:
        twins = [single[_twin_key(r.case)] for r in rows if _twin_key(r.case) in single]
        adv_rate = _rate(leaked)
        base_rate = _rate(t.injection_leaked for t in twins)
        return {
            "cases_enumerated": len(rows),
            "cases_leaked": sum(1 for f in leaked if f),
            "paired_single_chunk_cases": len(twins),
            "leak_rate_adversarial": adv_rate,
            "leak_rate_single_chunk_paired": base_rate,
            "delta_frag": round(adv_rate - base_rate, 4),
        }

    families: dict[str, dict[str, Any]] = {}
    for family in PARTITION_FAMILIES:
        enumerated = [r for r in adversarial if r.partitions_attempted.get(family)]
        capped = [r for r in adversarial if r.partitions_capped.get(family)]
        # Record families that were requested but empty for short values.
        too_short = [
            r for r in adversarial
            if family in r.partitions_attempted
            and not r.partitions_attempted[family]
            and not r.partitions_capped.get(family)
        ]
        if not enumerated and not capped and not too_short:
            continue
        block = _arm(enumerated, [r.partitions_leaked.get(family, 0) > 0 for r in enumerated])
        block.update(
            {
                "enumerated": bool(enumerated),
                "partitions_attempted": sum(
                    r.partitions_attempted.get(family, 0) for r in enumerated
                ),
                "partitions_leaked": sum(
                    r.partitions_leaked.get(family, 0) for r in enumerated
                ),
                "cases_capped": len(capped),
                "cases_value_too_short": len(too_short),
            }
        )
        families[family] = block

    union_families = sorted(f for f in families if families[f]["enumerated"])
    union_rows = [
        r
        for r in adversarial
        if any(r.partitions_attempted.get(f) for f in union_families)
        and not any(r.partitions_capped.get(f) for f in union_families)
    ]
    if union_families:
        worst = _arm(union_rows, [r.injection_leaked for r in union_rows])
        # Restrict component checks to the union denominator.
        on_union = {
            family: _rate(r.partitions_leaked.get(family, 0) > 0 for r in union_rows)
            for family in union_families
        }
        worst["component_leak_rates_on_union_denominator"] = on_union
        worst["never_below_components"] = all(
            worst["leak_rate_adversarial"] >= rate for rate in on_union.values()
        )
    else:
        # Return null worst-case stats if no family was enumerated.
        worst = {
            "cases_enumerated": 0,
            "cases_leaked": 0,
            "paired_single_chunk_cases": 0,
            "leak_rate_adversarial": None,
            "leak_rate_single_chunk_paired": None,
            "delta_frag": None,
            "component_leak_rates_on_union_denominator": {},
            "never_below_components": True,
        }
    worst.update(
        {
            "definition": (
                "case rate under the union of the enumerated partition families over the "
                "measured corpus values; NOT a worst case over arbitrary streams, values, "
                "interleavings, or partitions into more than three pieces"
                if union_families
                else "no partition family was enumerated by this oracle, so no "
                "union-based worst-case statistic is defined for this run; the rates are "
                "null rather than zero because they were not measured, and NOT a worst "
                "case over arbitrary streams"
            ),
            "families_in_union": union_families,
            "cases_excluded_by_cap": len(adversarial) - len(union_rows) if union_families else 0,
        }
    )

    adversarial_partitions = sum(
        r.split_points_tried for r in results if r.case["fragmentation"] == "adversarial"
    )
    uncut = sum(1 for r in results if r.case["fragmentation"] == "single_chunk")
    total = sum(r.split_points_tried for r in results)

    if oracle == "midpoint":
        sentence = (
            "Fragmentation is a two-part split at the value midpoint, not every split "
            "point (" + str(adversarial_partitions) + " midpoint partitions over "
            + str(uncut) + " uncut single-chunk requests; "
            + str(total) + " captured requests total)."
        )
    else:
        what = {
            "exhaustive-2-part": "every internal two-part split of the value",
            "exhaustive-3-part": "every internal three-part partition of the value",
            "union-worst-case": (
                "the union of every internal two-part split and every internal "
                "three-part partition of the value"
            ),
        }[oracle]
        sentence = (
            "Fragmentation is " + what + " ("
            + str(adversarial_partitions) + " internal adversarial partitions over "
            + str(len([r for r in results if r.case["fragmentation"] == "adversarial"]))
            + " adversarial cases, plus " + str(uncut) + " uncut single-chunk requests = "
            + str(total) + " captured requests total); a case leaks if any enumerated "
            "partition leaks. Bounded to these corpus values and these partition "
            "families, not to arbitrary streams."
        )

    return {
        "oracle": oracle,
        # Read the cap from every result.
        "resource_cap_per_case_per_family": (
            caps.pop() if len(caps) == 1 else max(caps, default=DEFAULT_PARTITION_CAP)
        ),
        "families": families,
        "worst_case": worst,
        "adversarial_partitions": adversarial_partitions,
        "uncut_single_chunk_requests": uncut,
        "captured_requests_total": total,
        "partition_seconds_total": round(sum(r.partition_seconds for r in results), 4),
        "cases_inconclusive_by_cap": sum(
            1 for r in adversarial if any(r.partitions_capped.values())
        ),
        "cases_inconclusive_in_transport": sum(
            1 for r in results if r.transport_error is not None
        ),
        "method_limit_sentence": sentence,
    }


def build_report(
    segments: Segments,
    results: list[RunResult],
    separation: dict[str, Any],
    seed: str,
    context: dict[str, Any] | None = None,
    axes: dict[str, tuple[str, ...]] | None = None,
    corpus: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    fixture: dict[str, Any] | None = None,
    claim: dict[str, Any] | None = None,
    schema_id: str = SCHEMA_ID,
) -> dict[str, Any]:
    """Assemble an http-profile report from the full covering array."""
    context = context or {}
    axes = AXES if axes is None else axes
    by_frag: dict[str, list[RunResult]] = {"single_chunk": [], "adversarial": []}
    for r in results:
        by_frag[r.case["fragmentation"]].append(r)

    # Filter out refused cases from denominators.
    scored = [r for r in results if r.transport_error is None]
    inconclusive = [r for r in results if r.transport_error is not None]
    by_frag = {k: [r for r in v if r.transport_error is None] for k, v in by_frag.items()}
    leak_single = _rate(r.injection_leaked for r in by_frag["single_chunk"])
    leak_adv = _rate(r.injection_leaked for r in by_frag["adversarial"])
    leak_overall = _rate(r.injection_leaked for r in scored)
    observable = [r for r in scored if r.echo_observable]
    # No observable case skips fidelity tracking entirely.
    fidelity = _rate(v for r in observable for v in r.echo_recovered.values()) if observable else 0.0
    delta_frag = round(leak_adv - leak_single, 4)
    # A negative delta_frag is possible due to false positives on fragments.

    case_defs = sorted(
        ({k: r.case[k] for k in sorted(axes)} for r in results),
        key=lambda c: tuple(sorted(c.items())),
    )
    digest = hashlib.sha256(
        json.dumps(case_defs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    covered: set[tuple[str, str, str, str]] = set()
    for r in results:
        covered |= _pairs_of(r.case, axes)
    # Exclude infeasible pairs from the required product.
    required = _all_pairs(axes, context.get("feasible"))

    # Identify entities the target completely misses even when unfragmented.
    detector_blind = {}
    for entity in axes["entity"]:
        baseline = [
            r for r in scored
            if r.case["entity"] == entity and r.case["fragmentation"] == "single_chunk"
        ]
        detector_blind[entity] = bool(baseline) and all(r.injection_leaked for r in baseline)

    leaked_types = sorted({r.case["entity"] for r in results if r.injection_leaked})
    latencies = [ms for r in results for ms in r.latency_ms]
    boundary = _boundary_check(results, segments)
    partition_oracle = _partition_oracle_block(results)
    boundary_leaked = bool(
        boundary["leaked_entity_types"] or boundary["unattributed_leaked_entity_types"]
    )

    def _axis_slice(axis: str) -> dict[str, dict[str, Any]]:
        """Per-axis-value leak and fidelity stats."""
        out: dict[str, dict[str, Any]] = {}
        for value in axes[axis]:
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
                # The denominator for fidelity_rate counts only observable sites.
                "echo_observable": len(visible),
                "leaked": sum(1 for r in rows if r.injection_leaked),
            }
        return out

    report = {
        "schema": schema_id,
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
        # Target details.
        "target": {
            "base_url": context.get("base_url", "http://127.0.0.1/v1 (ephemeral loopback)"),
            "model": context.get("model", "test"),
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
            # 0 means an ephemeral port per case.
            "port": context.get("capture_port", 0),
            # Honest claim about the capture's own authentication.
            "authentication_required": CAPTURE_REQUIRES_AUTHENTICATION,
            "target_must_be_preconfigured_for": "the ephemeral loopback upstream started by this module",
            # Measured. `_self_probe` sends a nonce to the capture before any target
            # traffic and aborts the run if this run's capture did not record it, which
            # is what the schema's `const true` on both fields has always assumed.
            "self_probe": {
                "performed": True,
                "url": results[0].self_probe_url or "http://127.0.0.1/__probe__",
                "recorded": True,
                "round_trip_ms": max(r.self_probe_ms for r in results),
            },
        },
        "checks": {
            "configured_upstream_boundary": boundary,
            "fragmentation_safety": _fragmentation_check(results, segments, axes),
            "sse_validity": _sse_check(results),
            "response_fidelity": _fidelity_check(observable, results),
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
        "passed": bool(
            leak_overall == 0.0
            and fidelity == 1.0
            and separation["passed"]
            and not boundary_leaked
        ),
        "limitations": {
            "run_validity": [
                "Reference response-path policies, not products. No third-party gateway is measured here.",
                "Loopback transport on a single machine; project-run and unreplicated.",
            ],
            "method_limits": [
                str(len(results)) + " cases: a pairwise covering array over the five axes, not exhaustive.",
                # THE DENOMINATOR, stated where the case count is stated. The line above
                # describes the ARRAY and was the only case count in this block, so a
                # reader had one number to divide by and it was the wrong one whenever
                # any case died in transport.
                (
                    "Every rate is over cases_applicable=" + str(len(scored))
                    + " of the " + str(len(results)) + " cases attempted; "
                    + str(len(inconclusive)) + " were inconclusive and are excluded from "
                    "the denominator rather than counted as no-leak."
                ),
                # DERIVED. This said "Three entity types" for as long as the corpus has
                # had four -- USPHONE was added to the entity axis and the sentence
                # describing the axes was not. A limitations block that describes a
                # narrower run than the one performed is the same defect as one that
                # describes a wider one; both are the report disagreeing with itself.
                ", ".join(
                    f"{len(values)} {axis}" for axis, values in axes.items()
                ) + ".",
                "Request sites are four shapes, not a survey of real client payloads.",
                # THREE NUMBERS, NOT ONE, AND THEY ARE NOT INTERCHANGEABLE. This field
                # read "252 splits over 16 adversarial cases" for every exhaustive row in
                # the published tree. 252 is the CAPTURED REQUEST count: 236 internal
                # adversarial partitions plus the 16 uncut single-chunk requests, which
                # are the baseline arm and are not splits of anything. The total came from
                # summing `split_points_tried` over BOTH arms, where a single-chunk case
                # contributes its one uncut attempt. The manuscript carried the correct
                # decomposition and the instrument contradicted it, which is a report
                # disagreeing with itself about its own method -- the same defect class as
                # `fragmentation_strategy` saying `exhaustive-2-part` while the code cut
                # once at the midpoint.
                partition_oracle["method_limit_sentence"],
                "Latency is loopback and in-process; it is not gateway overhead on a network.",
            ],
        },
        # OVERRIDABLE for the same reason. `claim_citation` names where a claim came
        # from, and a row for a policy defined in another module cited THIS module's
        # docstrings -- the same defect that was fixed once already when a third-party
        # gateway row cited these docstrings as the source of a vendor's claim.
        "redaction_claim": claim or {
            "vendor_claims_pii_redaction": "claimed",
            # A row for a third-party gateway used to cite THIS MODULE'S OWN policy
            # docstrings as the source of that vendor's redaction claim. The emitter
            # cannot know what a vendor claims, so for an external target it says where
            # the claim came from instead of inventing a citation.
            "claim_citation": (
                "pii_leak_benchmark.v2_emitter policy docstrings"
                if results[0].policy in POLICIES
                else "operator-supplied; see the run recipe in the target's profile directory"
            ),
            "configured_for_this_run": True,
            "configuration_reference": (
                "POLICIES[" + repr(results[0].policy) + "]"
                if results[0].policy in POLICIES
                else "external gateway configured by the operator; see the run script"
            ),
            "recorded_by": "operator",
            # Operator-supplied, from V2_REQUEST_PATH_REDACTION. Only the operator knows
            # what they configured, and without it a request-path leak cannot be
            # attributed: a gateway set up with response-side guardrails only was never
            # asked to mask the request. Reporting that as a coverage defect would be
            # measuring this repository's config file rather than the product -- the
            # mistake this README has already recorded twice.
            "request_path_redaction_configured": os.environ.get(
                "V2_REQUEST_PATH_REDACTION", "unknown"
            ),
        },
        "outcome": _derive_outcome(
            leak_overall, fidelity, separation["passed"], boundary_leaked
        ),
        # EVERY RATE CARRIES ITS OWN DENOMINATOR, and the denominator is
        # `cases_applicable` -- never `cases_scored`, which is `len(results)`, the number
        # of cases ATTEMPTED including the ones that died in transport.
        #
        # This line is the report's human-readable summary and the one a reader quotes,
        # and it published four rates with no denominator at all while the same report's
        # `method_limits` said "32 cases" three fields away. A run in which eight cases
        # were refused divides by 24 and announces 32, so a gateway that refuses the
        # cases it handles worst reads as a gateway that handled them. `manuscript-v3.md`
        # C6 states the rule this violated -- a rate without its denominator is not a
        # measurement -- and it was being violated by the field that states the result.
        #
        # The three rates have three DIFFERENT denominators and saying so is the point:
        # fidelity is over the echo-observable cases (a gateway that drops the field
        # presented nothing to restore), and the two leak rates are over their own
        # fragmentation arm, not over the whole array.
        "outcome_rationale": (
            "FidelityRate=" + str(fidelity)
            + " over " + str(len(observable)) + " echo-observable"
            + ", LeakRate(single_chunk)=" + str(leak_single)
            + " over " + str(len(by_frag["single_chunk"]))
            + ", LeakRate(adversarial)=" + str(leak_adv)
            + " over " + str(len(by_frag["adversarial"]))
            + ", DeltaFrag=" + str(delta_frag)
            + ", request-path leak=" + (
                ",".join(boundary["leaked_entity_types"]) or "none"
            )
            + ". Rates are over cases_applicable=" + str(len(scored))
            + " of " + str(len(results)) + " attempted ("
            + str(len(inconclusive)) + " inconclusive, excluded from every denominator "
            "rather than counted as no-leak)."
        ),
        # OVERRIDABLE, because a profile with a different needle set has a different
        # fixture and this block would otherwise describe half of it. The FIDE corpus
        # carries four fixed secret literals alongside the four generated PII values;
        # publishing the PII description alone would say the run drew from a value space
        # it did not use and would omit four of its eight needles entirely.
        "fixture": fixture or {
            "varies_per_run": True,
            "values_published": False,
            # Frozen-report metadata debt: USPHONE is enabled and represented in
            # value_space_nominal but is missing from this descriptive formats map.
            # Adding it changes instrumented build_report source and therefore requires
            # a new evidence release rather than an in-place metadata rewrite.
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
            "id": (corpus or {}).get("id", "minimal-response-split"),
            "version": (corpus or {}).get("version", "0.1.0"),
            "sha256": digest,
            "case_count": len(results),
            "seed": seed,
            "coverage": {
                "strategy": "pairwise",
                "axes": sorted(axes),
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
            # Set below, from `_assert_derivations`, AFTER this block exists -- because
            # what they promise is a check of this block, and a check that runs on the
            # values it is about to publish has to be able to see them. Assigning them
            # inline is how the first repair ended up recomputing `leak_adv -
            # leak_single` from the caller's own two variables.
            "derivation_recomputed": False,
            "sidecar_case_count_matches": False,
            "by_axis": {axis: _axis_slice(axis) for axis in axes},
            # Explicitly state what the oracle enumerated to contextualize rates.
            "partition_oracle": partition_oracle,
            # Per axis value, the two arms separately. See `_axis_arms`.
            "by_axis_arm": _axis_arms(results, axes),
            # The paired 2x2 table underlying DeltaFrag. See `_discordance`.
            "discordance": _discordance(results),
        },
        "entity_scope": scope or {
            "mechanism": "reference-policy detector set",
            "enabled": [name for name, _ in _DETECTORS],
            "not_enabled": [],
            "unknown": [],
            "partitions_corpus": True,
            "recorded_by": "operator",
            "source": "pii_leak_benchmark.v2_emitter._DETECTORS",
        },
        "cases_digest": digest,
        # See `inspector_digest`.
        "instrument": instrument_block(),
    }

    # Ensure generated derivations match raw data before returning.
    verified = _assert_derivations(results, report["metrics"], digest, axes)
    report["metrics"]["derivation_recomputed"] = verified
    report["metrics"]["sidecar_case_count_matches"] = verified
    return report


def _value_space() -> dict[str, int]:
    """Distinct values each entity draws from, incorporating v2-specific entities."""
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
    # USPHONE combinations from make_seeded_fixture.
    out["USPHONE"] = 8 * 100 * 100
    return out or {"EMAIL": 1}


def _derive_outcome(
    leak_adv: float, fidelity: float, separated: bool, boundary_leaked: bool = False
) -> str:
    """Outcome derived from both leak rates and boundary check results."""
    if not separated:
        return "inconclusive"
    if leak_adv > 0.0 or boundary_leaked:
        return "fail"
    if fidelity < 1.0:
        return "no-leak-profile-not-met"
    return "pass"


# --------------------------------------------------------------------------------------
# What the client-side inspector actually does, as tested data.
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
        "ordered_whole_document_join",
        "every string value reassembled in document order, so a value split between "
        "delta content and any other field is recovered",
    ),
    InspectionCapability(
        "renderable_subtree_join",
        "content and text members of one content subtree reassembled together, so a "
        "value split between a content string and a content list of parts is recovered",
    ),
    InspectionCapability(
        "unparseable_events", "events that do not parse as JSON scanned as raw text"
    ),
    InspectionCapability(
        "shadowed_duplicate_keys",
        "events carrying duplicate JSON object names also scanned as raw text, because "
        "parsing discards every value but the last",
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


# What the request-path inspector actually does, as tested data.
BOUNDARY_INSPECTION_CAPABILITIES: tuple[InspectionCapability, ...] = (
    InspectionCapability(
        "boundary_every_request",
        "every request body this run's capture recorded, across all cases",
    ),
    InspectionCapability("boundary_json_parsed", "bodies parsed as JSON"),
    InspectionCapability(
        "boundary_recursive_walk",
        "walked recursively over all JSON types, including nested objects, lists, "
        "numbers and object keys",
    ),
    InspectionCapability(
        "boundary_ordered_join",
        "strings reassembled in arrival order per JSON path, across captured requests, "
        "with decoded material held out of the ordered stream",
    ),
    InspectionCapability(
        "boundary_unparseable",
        "bodies that do not parse as JSON scanned as raw text",
    ),
    InspectionCapability(
        "boundary_decoded",
        "base64/hex/percent-encoded runs and character-code arrays decoded",
    ),
    InspectionCapability(
        "boundary_normalized",
        "matched literally and with separators removed, over NFKD-decomposed text with "
        "non-Latin digits resolved to their decimal value and non-ASCII look-alikes "
        "folded to ASCII per UTS #39",
    ),
    InspectionCapability(
        "boundary_correlation",
        "correlated by in-process capture identity rather than by marker words, so "
        "marker_words_observed_max is 0 by construction and is not a coverage signal",
    ),
)

BOUNDARY_INSPECTION_SCOPE = "; ".join(c.clause for c in BOUNDARY_INSPECTION_CAPABILITIES)


# --------------------------------------------------------------------------------------
# Fingerprint of enumerated scorer source.
# --------------------------------------------------------------------------------------

_INSTRUMENTED = (
    "_parse_sse",
    "_sse_frames",
    "_injection_events",
    "_make_upstream",
    "_ordered_channels",
    "_haystack_groups",
    "_haystacks",
    "_leak_tier",
    "_present",
    "_boundary_haystacks",
    "_boundary_evidence",
    "_boundary_check",
    "_fidelity_check",
    "_sse_check",
    "_fragmentation_check",
    # Functions that decide published fields.
    "_one_character_events",
    # Core capture response tracking.
    "_coalescing_rows",
    "_injection_check",
    "_injection_evidence",
    "_derive_outcome",
    "_assert_derivations",
    "_count_invalid_events",
    "_rate",
    # The partition oracle logic.
    "_family_partitions",
    "injection_partitions",
    "_partition_pieces",
    "_partition_oracle_block",
    "_fragmentation_strategy_label",
    "_twin_key",
    "_axis_arms",
    "_discordance",
    # Entity value space config.
    "_value_space",
    "run_case",
    "build_report",
)


def _behaviour_source(function: Any) -> str:
    """One function's source with comments, docstrings and formatting normalised away."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
            and isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def inspector_digest() -> str:
    """Fingerprint the enumerated scorer source; return 16 SHA-256 hex characters."""
    from pii_leak_benchmark import http_profile

    parts = [_behaviour_source(globals()[name]) for name in _INSTRUMENTED]
    # Include decoding and folding logic from v1.
    parts += [
        _behaviour_source(getattr(http_profile, name))
        for name in ("_collect", "_normalize", "_normalize_confusable_digits")
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def instrument_block() -> dict[str, str]:
    """The provenance block every artefact carries, single-run and sweep alike."""

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    return {
        "emitter_version": EMITTER_VERSION,
        "client_scope_sha256": digest(CLIENT_INSPECTION_SCOPE),
        "boundary_scope_sha256": digest(BOUNDARY_INSPECTION_SCOPE),
        # Digest sensitive to behavior changes.
        "inspector_sha256": inspector_digest(),
    }


def _boundary_haystacks(bodies: Iterable[str]) -> list[str]:
    """The request-path equivalent of `_haystacks`, over every captured body."""
    from pii_leak_benchmark.http_profile import _Inspection, _collect  # noqa: I001

    found = _Inspection()
    flat: list[tuple[str, str]] = []
    unparseable = 0
    for body in bodies:
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            unparseable += 1
            _collect(body, found)
            continue
        _ordered_channels(parsed, flat)
        _collect(parsed, found)
    channels: dict[str, list[str]] = {}
    for path, value in flat:
        channels.setdefault(path, []).append(value)
    siblings = list(found.strings) + list(found.decoded_strings)
    out = [
        "".join(value for _, value in flat),
        *("".join(values) for values in channels.values()),
        "".join(siblings),
        *siblings,
    ]
    from urllib.parse import unquote

    return [*out, *(unquote(h) for h in out), str(unparseable)]


def _boundary_evidence(
    bodies: list[str], per_body: list[list[str]], all_bodies: list[str], needle: str
) -> tuple[str, str, str] | None:
    """The strongest provenance available for one value, or None if it never egressed."""
    for body in bodies:
        if needle in body:
            return ("body", "per-request", "literal")
    for haystacks in per_body:
        if _present(needle, haystacks):
            return ("body", "per-request", "normalized")
    if _present(needle, all_bodies):
        return ("body", "cross-request", "normalized")
    return None


def _boundary_check(results: list[RunResult], segments: Segments) -> dict[str, Any]:
    """Check if protected values reached upstream unmasked."""
    bodies = [body for result in results for body in result.upstream_bodies]
    unparseable = 0
    for body in bodies:
        try:
            json.loads(body)
        except (ValueError, TypeError):
            unparseable += 1

    per_body = [_boundary_haystacks([body]) for body in bodies]
    all_bodies = _boundary_haystacks(bodies)

    evidence: list[dict[str, str]] = []
    leaked: list[str] = []
    for name, value in sorted(segments.echo.items()):
        found = _boundary_evidence(bodies, per_body, all_bodies, value)
        if found is None:
            continue
        leaked.append(name)
        channel, scope, match = found
        evidence.append(
            {"entity_type": name, "channel": channel, "scope": scope, "match": match}
        )

    # Check for test control contamination.
    contaminated: list[str] = []
    contaminated_evidence: list[dict[str, str]] = []
    for name, value in sorted(segments.injection.items()):
        found = _boundary_evidence(bodies, per_body, all_bodies, value)
        if found is None:
            continue
        contaminated.append(name)
        channel, scope, match = found
        contaminated_evidence.append(
            {"entity_type": name, "channel": channel, "scope": scope, "match": match}
        )

    return {
        "passed": not leaked and not contaminated and unparseable == 0 and bool(bodies),
        "captured_requests": len(bodies),
        "correlated_requests": len(bodies),
        "uninspectable_requests": unparseable,
        "uninspectable_reasons": (["body did not parse as JSON"] if unparseable else []),
        "leaked_entity_types": leaked,
        # Observed upstream paths.
        "upstream_paths_observed": sorted({p for r in results for p in r.upstream_paths}),
        "marker_words_required": 3,
        "marker_words_total": 5,
        # Captured in-process, marker words not needed.
        "marker_words_observed_max": 0,
        "payload_content_included": False,
        "inspection_scope": BOUNDARY_INSPECTION_SCOPE,
        "capture_mode": "loopback",
        "correlation_mechanism": "in-process-capture",
        "unattributed_requests": 0,
        "unattributed_uninspectable_requests": 0,
        "unattributed_leaked_entity_types": contaminated,
        "unattributed_leak_evidence": contaminated_evidence,
        "leak_evidence": evidence,
        "needle_proximity": {},
        "needle_lengths": {},
    }


def _one_character_events(segments: Segments, results: list[RunResult]) -> bool:
    """Check if a single-character data event was actually emitted."""
    for result in results:
        if result.case.get("fragmentation") != "adversarial":
            continue
        rendered = _encode(
            segments.injection[result.case["entity"]], result.case["encoding"]
        )
        points, _families, _attempted, _capped = injection_partitions(
            segments, result.case, oracle=result.oracle, cap=result.partition_cap
        )
        for cuts in points:
            if not cuts:
                continue
            if any(len(piece) == 1 for piece in _partition_pieces(rendered, cuts)):
                return True
    return False


def _coalescing_rows(
    results: list[RunResult], axes: dict[str, tuple[str, ...]] | None = None
) -> list[dict[str, Any]]:
    """Record events emitted vs observed and the coalescing verdict per case."""
    rows: list[dict[str, Any]] = []
    for result in results:
        upstream = result.upstream_data_events
        observed = result.data_events_observed
        failed = result.transport_error is not None or observed == 0
        comparison_available = (
            not failed
            and upstream > 0
            and result.upstream_responses_observed == 1
        )
        rows.append({
            "case": {k: result.case[k] for k in sorted(AXES if axes is None else axes)},
            "upstream_data_events_emitted": upstream,
            "upstream_responses_observed": result.upstream_responses_observed,
            "data_events_observed": observed,
            # Track transport failures distinctly.
            "stream_failure": failed,
            "coalesced": observed < upstream if comparison_available else None,
        })
    return rows


def _fragmentation_check(
    results: list[RunResult],
    segments: Segments,
    axes: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Event counts over the whole array, reporting coalescing as a rate."""
    scored = [r for r in results if r.transport_error is None]
    counts = [max(r.events_observed, r.events_observed_max) for r in scored] or [0]
    rows = _coalescing_rows(results, axes)
    comparable = [r for r in rows if r["coalesced"] is not None]
    coalesced = [r for r in comparable if r["coalesced"]]
    failures = [r for r in rows if r["stream_failure"]]
    return {
        # Ensure we check for actual data-bearing fragmentation.
        "passed": bool(
            scored
            and all(r.data_events_observed > 1 and r.client_text for r in scored)
        ),
        # Derive whether single-character events were requested.
        "one_character_events_requested": _one_character_events(segments, results),
        # Extrema over the scored array.
        "events_observed": min((r.events_observed for r in scored), default=0),
        "events_observed_max": max(counts),
        "data_events_observed": min((r.data_events_observed for r in scored), default=0),
        # Coalescing is distinguished since v2 controls the upstream.
        "coalescing_not_distinguished": False,
        # Empirical sums of events.
        "upstream_data_events_emitted_total": sum(
            r["upstream_data_events_emitted"] for r in rows
        ),
        "data_events_observed_total": sum(r["data_events_observed"] for r in rows),
        # Rate computed over valid comparisons.
        "coalescing_rate": (
            round(len(coalesced) / len(comparable), 4) if comparable else None
        ),
        "coalescing_cases": len(coalesced),
        "coalescing_cases_compared": len(comparable),
        # Stream failures indicate no events arrived.
        "stream_failure": bool(failures),
        "stream_failure_cases": len(failures),
        "coalescing_per_case": rows,
        "response_reconstructed": bool(scored) and all(bool(r.client_text) for r in scored),
    }


def _sse_check(results: list[RunResult]) -> dict[str, Any]:
    """Framing facts validated against responses."""
    statuses = sorted({code for r in results for code in r.status_codes})
    types = sorted({t for r in results for t in r.content_types})
    invalid = sum(r.invalid_events for r in results)
    scored = [r for r in results if r.transport_error is None]
    bad_types = sorted(
        {t for t in types if not t.split(";")[0].strip().lower() == "text/event-stream"}
    )
    missing_done = [r.case for r in scored if not r.done_marker]
    # Unanswered cases fail the validation.
    unanswered = [r for r in results if r.transport_error is not None]
    errors: list[str] = []
    if bad_types:
        errors.append("content type not text/event-stream: " + ", ".join(bad_types))
    if missing_done:
        errors.append(f"{len(missing_done)} scored cases ended without a [DONE] event")
    if invalid:
        errors.append(f"{invalid} dispatched events did not parse as JSON")
    if unanswered:
        errors.append(
            f"{len(unanswered)} cases produced no complete response, so their framing "
            "was never observed"
        )
    if not results:
        errors.append("no cases were run")
    return {
        "passed": not errors and statuses == [200],
        "invalid_events": invalid,
        "done_markers_valid": not missing_done,
        "content_type_valid": not bad_types and bool(types),
        "status_codes": statuses or [0],
        "errors": errors,
    }


def _fidelity_check(observable: list[RunResult], attempted: list[RunResult]) -> dict[str, Any]:
    """Echo fidelity over cases where echo was measurable."""
    matching = sum(1 for r in observable for v in r.echo_recovered.values() if v)
    completed = sum(len(r.echo_recovered) for r in observable)
    requested = sum(len(r.echo_recovered) for r in attempted)
    return {
        "passed": completed > 0 and matching == completed,
        "expected_value_reconstructed": completed > 0 and matching == completed,
        "iterations_matching": matching,
        "iterations_completed": completed,
        "iterations_requested": requested,
        "payload_content_included": False,
        "segment": "echo",
    }


def _injection_evidence(
    results: list[RunResult], leaked_types: list[str]
) -> list[dict[str, str]]:
    """Strongest recovery tier per leaked entity, and how many cases reached it."""
    evidence: list[dict[str, str]] = []
    for entity in leaked_types:
        tiers = [
            r.leak_tier
            for r in results
            if r.case["entity"] == entity and r.injection_leaked and r.leak_tier
        ]
        best = min(tiers, key=lambda t: _LEAK_TIER_RANK[t]) if tiers else "unrecorded"
        evidence.append(
            {
                "entity_type": entity,
                "observed": best,
                "cases_leaked": str(len(tiers)),
                # Highlight concatenation.
                "weakest_tier_is_a_concatenation": str(
                    all(t == "cross-field-join" for t in tiers) if tiers else False
                ).lower(),
            }
        )
    return evidence


def _fragmentation_strategy_label(results: list[RunResult]) -> str:
    """Fragmentation strategy recorded during the run."""
    # EVERY result, not just the adversarial ones. `run_case` records the oracle on both
    # arms, and a run whose adversarial cases all died in transport would otherwise be
    # published under the midpoint label whatever oracle was actually requested.
    oracles = {r.oracle for r in results}
    if len(oracles) != 1:
        # Mixed or empty. `across-sse-events` is the weakest true statement available:
        # the value was placed in separate SSE events and nothing stronger is claimed.
        return "across-sse-events"
    oracle = oracles.pop()
    return {
        "midpoint": "across-sse-events",
        "exhaustive-2-part": "exhaustive-2-part",
        "exhaustive-3-part": "exhaustive-3-part",
        "union-worst-case": "union-worst-case",
    }[oracle]


def _injection_check(results: list[RunResult], leaked_types: list[str]) -> dict[str, Any]:
    # A case the client never saw a response for cannot testify to containment. The
    # schema already forbids `passed` alongside `delivery_confirmed: false`; tying them
    # here means the emitter cannot produce that contradiction in the first place.
    delivery_confirmed = bool(results) and all(bool(r.client_text) for r in results)
    return {
        "passed": (not leaked_types) and delivery_confirmed,
        "segment": "injection",
        # WHAT WAS ACTUALLY DONE. This said "exhaustive-2-part" while the code cut the
        # value once at its midpoint, and `limitations.method_limits` in the same report
        # said "not every split point". A report cannot contradict itself about its own
        # method; the label is now derived from the split points that were run.
        "fragmentation_strategy": _fragmentation_strategy_label(results),
        "injected_entity_types": sorted({r.case["entity"] for r in results}),
        "leaked_entity_types": leaked_types,
        # HOW each value was recovered, strongest claim per entity. Every entry used to
        # read `observed: normalized-match` whether the value arrived verbatim in one
        # event or was assembled by concatenating two unrelated fields -- the same
        # objection `_boundary_evidence` answers on the request path and this check did
        # not. `cross-field-join` is the tier a coincidence can reach; it is the one a
        # reader should discount first.
        "leak_evidence": _injection_evidence(results, leaked_types),
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
    oracle: str = "midpoint",
    partition_cap: int = DEFAULT_PARTITION_CAP,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one policy across the whole covering array and emit its v2 report.

    Within a pinned implementation, `seed` reproduces the generated fixture selection.
    It does not freeze target binaries, environment, timestamps, ports, or timings.
    Omitted, a fresh seed is drawn from `secrets` so successive runs still vary.
    """
    import secrets

    if iterations != 1:
        raise ValueError(
            "v2 run_policy supports exactly one response observation per case; "
            "multi-iteration leak aggregation requires a new instrument/evidence release"
        )
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
            oracle=oracle,
            partition_cap=partition_cap,
        )
        for case in covering_array()
    ]
    report = build_report(
        segments,
        results,
        separation,
        seed,
        context={
            "base_url": gateway_url or "in-process loopback reference policy",
            "model": model,
            "capture_port": upstream_port,
        },
    )
    summary = {
        "fidelity_rate": report["metrics"]["fidelity_rate"],
        "leak_single_chunk": report["metrics"]["leak_rate"]["single_chunk"],
        "leak_adversarial": report["metrics"]["leak_rate"]["adversarial"],
        "delta_frag": report["metrics"]["delta_frag"],
        # THE DENOMINATOR FIRST, and named so it cannot be mistaken for the run size.
        # This key was `cases` = `cases_scored` = `len(results)`, and it is what the
        # sweep writes into every row of `seed-sweep.json` -- the file the README calls
        # "the numbers to cite". Four rates over the applicable cases, published beside a
        # case count that is the attempted cases, is the same defect as the rationale
        # line: the reader is handed a denominator that is not the one used.
        "cases_applicable": report["metrics"]["cases_applicable"],
        # The run size, kept because the all-cases-refused guard below needs the total
        # and because "24 of 32" is more informative than either number alone. NOT a
        # denominator, and no longer spelled in a way that invites use as one.
        "cases_attempted": report["metrics"]["cases_scored"],
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


def _write_report(path: Any, report: dict[str, Any]) -> None:
    """Write canonical UTF-8 JSON with LF endings and one trailing newline.

    Kept as a named local alias because `main` is not the only thing that has ever
    written a report here, and because the round-two review found that fixing this
    hazard in one writer at a time is how it keeps coming back. The implementation
    lives in `artifact` so the v2/FIDE emitters and their sweep drivers share one
    implementation. V1 retains its own explicit-LF writer.
    """
    from pii_leak_benchmark.artifact import write_json_artifact

    write_json_artifact(path, report, indent=1)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        help="output directory (REQUIRED; use a scratch directory for verification runs)",
    )
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated policy names")
    parser.add_argument(
        "--seed",
        default=None,
        help="hex seed; reproduces fixture selection within the pinned implementation",
    )
    parser.add_argument("--gateway-url", default=None, help="external gateway chat-completions URL")
    parser.add_argument("--upstream-port", type=int, default=0, help="fixed capture port")
    parser.add_argument("--model", default="test", help="model name the gateway routes on")
    parser.add_argument(
        "--exhaustive-splits",
        action="store_true",
        help=(
            "alias for --oracle exhaustive-2-part. Kept because every published run "
            "recipe and rerun script names it; a flag that silently stopped working "
            "would re-measure a row under an oracle its own recipe does not describe."
        ),
    )
    parser.add_argument(
        "--oracle",
        default=None,
        choices=list(ORACLES),
        help=(
            "which partition family to enumerate for each adversarial case. "
            "midpoint: one two-part cut at len//2 (the published default). "
            "exhaustive-2-part: every internal two-part split, N-1 per value. "
            "exhaustive-3-part: every internal three-part partition, choose(N-1,2). "
            "union-worst-case: both, with the case failing if ANY enumerated partition "
            "leaks. 'Worst case' is bounded to these corpus values and these families."
        ),
    )
    parser.add_argument(
        "--partition-cap",
        type=int,
        default=DEFAULT_PARTITION_CAP,
        help=(
            "per case per family enumeration ceiling. A family that would exceed it is "
            "NOT shortened: the case is inconclusive for that family and is excluded "
            "from its denominator. An aborted combinatorial run must not score as "
            "containment."
        ),
    )
    args = parser.parse_args(argv)

    # The alias and the flag must not disagree silently. A recipe that says one thing and
    # a run that does another is how `fragmentation_strategy` came to claim
    # `exhaustive-2-part` for a midpoint cut.
    if args.exhaustive_splits and args.oracle not in (None, "exhaustive-2-part"):
        parser.error(
            "--exhaustive-splits is an alias for --oracle exhaustive-2-part and "
            f"contradicts --oracle {args.oracle}"
        )
    oracle = args.oracle or ("exhaustive-2-part" if args.exhaustive_splits else "midpoint")

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
            oracle=oracle,
            partition_cap=args.partition_cap,
        )
        errors = validator(report) if validator else []
        path = outdir / f"{name}.json"
        _write_report(path, report)
        rows.append((name, summary, report["outcome"], errors))
        status = "VALID" if validator and not errors else ("INVALID" if errors else "-")
        print(
            f"{name:20} fidelity={summary['fidelity_rate']:<6} "
            f"leak_single={summary['leak_single_chunk']:<6} "
            f"leak_adv={summary['leak_adversarial']:<6} "
            f"DeltaFrag={summary['delta_frag']:<7} "
            # Include denominator for rates in console output.
            f"n={summary['cases_applicable']}/{summary['cases_attempted']:<6} "
            f"outcome={report['outcome']:<24} schema={status}"
        )
        for err in errors[:6]:
            print("      !", err)
    return 0 if all(not r[3] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
