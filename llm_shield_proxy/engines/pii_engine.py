"""Enterprise 3-Tier PII Detection & Redaction Engine.

Implements a high-throughput multi-tier detection cascade:
- Tier 1: Microsecond pre-compiled DFA regular expressions for structured secrets & numbers.
- Tier 2: Shannon Entropy filter (tau_H >= 4.5 bits/symbol) for unformatted raw cryptographic keys.
- Tier 3: Contextual Named Entity Recognition (NER) via a loaded ONNX model, or nothing at all.
"""

from __future__ import annotations

import base64
import html
import logging
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

import yaml

from llm_shield_proxy.core.config import request_policy_ctx, settings
from llm_shield_proxy.core.config_schema import CustomRegexConfig
from llm_shield_proxy.engines.confusables import CONFUSABLE_TO_ASCII
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.observability.tracing import tracer

try:
    import re2  # type: ignore
except ImportError:
    re2 = None

logger = logging.getLogger(__name__)


@dataclass
class CompiledProfile:
    name: str
    tier1_patterns: List[Tuple[str, Any]] = field(default_factory=list)
    tier3_ner_entities: Set[str] = field(default_factory=set)


# Characters that render as nothing and so hide a value from every pattern while the
# client still displays the real thing.
INVISIBLE_CHARS_PATTERN: re.Pattern[str] = re.compile(
    "["
    "\u00AD"  # soft hyphen
    "\u034F"  # combining grapheme joiner
    "\u061C"  # Arabic letter mark
    "\u115F\u1160"  # Hangul choseong/jungseong fillers
    "\u17B4\u17B5"  # Khmer inherent vowels, invisible
    "\u180E"  # Mongolian vowel separator, a format character with no glyph
    # NOT U+180B-U+180D. Those are Mongolian Free Variation Selectors and they SELECT
    # GLYPH VARIANTS in ordinary Mongolian text. This class is deleted from what gets
    # FORWARDED, so including them silently rewrote real Mongolian input before it
    # reached the provider. Same rule that keeps U+FE0F and U+2800 out: invisible is
    # not sufficient, the character must also have no role in ordinary prose.
    "\u200B-\u200F"  # zero-width space through RTL mark
    "\u202A-\u202E"  # bidi embedding and override
    "\u2060-\u206F"  # word joiner, invisible operators, deprecated format chars
    "\u3164"  # Hangul filler
    "\uFEFF"  # zero-width no-break space
    "\uFFA0"  # halfwidth Hangul filler
    "\U000e0000-\U000e007f"  # tag block, the classic ASCII smuggler
    "]"
)

# Candidate base64 patterns for obfuscated PII smuggling.
BASE64_CANDIDATE_PATTERN: re.Pattern[str] = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{8,}={0,2}(?![A-Za-z0-9+/=_-])")
MAX_BASE64_INSPECTION_CHARS = 8_192
BASE64_BOUNDARY_SCAN_CHARS = 256
# How many times a candidate is decoded before giving up.
MAX_BASE64_DECODE_DEPTH = 3

# Percent-encoding hides PII from every Tier 1 pattern.
PERCENT_ESCAPE_PATTERN: re.Pattern[str] = re.compile(r"%[0-9A-Fa-f]{2}")
MAX_PERCENT_INSPECTION_CHARS = 8_192
# A run longer than the limit is not skipped. Its edges are still decoded.
PERCENT_BOUNDARY_SCAN_CHARS = 256
# Finds runs of non-delimiter characters in one C-level pass.
PERCENT_RUN_PATTERN: re.Pattern[str] = re.compile(r"[^\s\"'<>{}\[\],;()]+")

# Cross-script look-alikes, from the UTS #39 confusables table.
_CONFUSABLE_TRANSLATION = str.maketrans(CONFUSABLE_TO_ASCII)

# HTML entities hide structured PII.
HTML_ENTITY_PATTERN: re.Pattern[str] = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
MAX_ENTITY_INSPECTION_CHARS = 8_192
# A run longer than the limit is not skipped. Its edges are still decoded.
ENTITY_BOUNDARY_SCAN_CHARS = 256
# The same 256-char edge probe, for a blob in a field no policy claims.
BLOB_BOUNDARY_SCAN_CHARS = 256
# Deliberately NOT `_PERCENT_RUN_DELIMITERS`.
_ENTITY_RUN_DELIMITERS = frozenset(" \t\r\n\f\v\"'<>{}[](),")

# Indirect prompt injection override patterns in tool / retrieval contexts
INDIRECT_PROMPT_INJECTION_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(?:system\s+override|ignore\s+all\s+previous\s+instructions|<\|im_start\|>system|<\|im_end\|>)\b"
)

# ASCII-only boundary assertions.
_DASH = r"[-\u2010-\u2014\u2212]"

_ASCII_LEFT_BOUNDARY = r"(?<![A-Za-z0-9_])"
_ASCII_RIGHT_BOUNDARY = r"(?![A-Za-z0-9_])"

# ---------------------------------------------------------------------------
# Structural validation of Tier 1 matches.
# ---------------------------------------------------------------------------

# Selected public payment-network identifiers.
_CARD_IIN_PREFIXES = (
    "4",                                     # Visa
    "34", "37",                              # American Express
    "30", "36", "38", "39",                  # Diners Club
    "35",                                    # JCB
    "51", "52", "53", "54", "55",            # Mastercard
    "6011", "62", "64", "65",                # Discover / UnionPay / Maestro
)
_CARD_MASTERCARD_2_SERIES = (222100, 272099)
# ISO/IEC 7812-1 permits a PAN of up to 19 digits.
_CARD_MIN_DIGITS = 13
_CARD_MAX_DIGITS = 19


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = ord(character) - 48
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_payment_iin(digits: str) -> bool:
    if digits.startswith(_CARD_IIN_PREFIXES):
        return True
    if len(digits) >= 6:
        head = int(digits[:6])
        if _CARD_MASTERCARD_2_SERIES[0] <= head <= _CARD_MASTERCARD_2_SERIES[1]:
            return True
    return False


def classify_tier1_match(entity_type: str, matched: str) -> Tuple[bool, str]:
    """Return (keep_the_span, confidence)."""
    if entity_type == "CREDIT_CARD":
        digits = "".join(character for character in matched if character.isdigit())
        if not _CARD_MIN_DIGITS <= len(digits) <= _CARD_MAX_DIGITS:
            return True, "medium"
        issuer = _is_payment_iin(digits)
        checksum = _luhn_ok(digits)
        if issuer and checksum:
            return True, "high"
        # Every regex-shaped card is redacted.
        return True, "medium"

    return True, "medium"


# Tier 1 Pre-Compiled Regex Patterns
TIER1_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        # The repetition limits stop a denial of service.
        "EMAIL",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    ("SSN", re.compile(
        _ASCII_LEFT_BOUNDARY + r"\d{3}" + _DASH + r"\d{2}" + _DASH + r"\d{4}" + _ASCII_RIGHT_BOUNDARY
    )),
    (
        "PHONE",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[.\s]?" + _DASH + r"?(?:\d{3}[.\s]?" + _DASH + r"?)?\d{4}"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    ("CREDIT_CARD", re.compile(
        _ASCII_LEFT_BOUNDARY + r"(?:\d[ ]?" + _DASH + r"?){13,19}" + _ASCII_RIGHT_BOUNDARY
    )),
    (
        # A digit run longer than any card.
        "LONG_DIGIT_RUN",
        re.compile(_ASCII_LEFT_BOUNDARY + r"\d{20,}" + _ASCII_RIGHT_BOUNDARY),
    ),
    (
        "IP_ADDRESS",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    (
        "AWS_API_KEY",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"(?:sk-[a-zA-Z0-9]{32,48}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16})"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    (
        "GITHUB_PAT",
        re.compile(
            _ASCII_LEFT_BOUNDARY + r"(?:ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]+)" + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    (
        "SLACK_TOKEN",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"x(?:ox[baprse]|app)-(?:[0-9a-zA-Z]+-)+[0-9a-zA-Z]+"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    (
        "SSH_PRIVATE_KEY",
        re.compile(r"-----BEGIN.*?PRIVATE KEY-----", re.DOTALL),
    ),
    (
        "JWT_TOKEN",
        re.compile(
            _ASCII_LEFT_BOUNDARY
            + r"ey[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*"
            + _ASCII_RIGHT_BOUNDARY
        ),
    ),
    ("MRN", re.compile(_ASCII_LEFT_BOUNDARY + r"\d{3}-\d{2}-\d{2}[A-Za-z0-9]" + _ASCII_RIGHT_BOUNDARY)),
]

# ---------------------------------------------------------------------------
# Tier 3: Contextual Named Entity Recognition.
# ---------------------------------------------------------------------------

# Entity types the Tier 3 model path can emit.
TIER3_NER_ENTITIES: Set[str] = {"PERSON"}

# The single wording for "names are not being redacted".
NER_DISABLED_WARNING = (
    "Name redaction is off. The Tier 3 NER model is not loaded, so people's names will "
    "not be redacted. Email addresses, card numbers, SSNs and other structured "
    "identifiers are still redacted normally. To redact names, set "
    "ENABLE_TIER3_ONNX_NER=true and point ONNX_MODEL_PATH at the model file."
)

# Candidate pattern for Shannon Entropy evaluation
# Candidate pattern for Shannon Entropy evaluation.
CANDIDATE_SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"(?<![A-Za-z0-9_\-+=])[A-Za-z0-9_\-+=]{16,}(?![A-Za-z0-9_\-+=])"
)


# ---------------------------------------------------------------------------
# No span may stop in the middle of a digit run.
# ---------------------------------------------------------------------------

# Separators that may appear inside a single printed identifier.
_DIGIT_RUN_SEPARATORS = "-. "


def _extend_span_over_digit_run(text: str, end: int, limit: int) -> int:
    """Grow a span rightwards while the digit run it ended in continues.

    ``limit`` is the start of the next accepted span (or the end of the text), so growth
    can never create an overlap or steal a neighbouring identifier.
    """
    while end < limit:
        character = text[end]
        if character.isdigit():
            end += 1
        elif (
            character in _DIGIT_RUN_SEPARATORS
            and end + 1 < limit
            and text[end + 1].isdigit()
        ):
            end += 2
        else:
            break
    return end


def _decode_base64_candidate(token: str) -> Optional[bytes]:
    """Decodes a base64 candidate in either alphabet, padded or not.

    The decode used to be a bare `b64decode(token, validate=True)`, which rejected
    two entirely ordinary spellings:

    - **Unpadded.** Encoders drop `=` routinely (JWT segments, URL parameters, a
      value that was `.rstrip("=")`-ed). `aaa@aaa.com` becomes `YWFhQGFhYS5jb20`,
      length 15, and `validate=True` raises on the length rather than decoding it.
    - **URL-safe**, which uses `-` and `_` where the standard alphabet uses `+`
      and `/`.

    Padding is restored arithmetically and the standard alphabet is tried first,
    since it is far more common. `validate=True` stays on in both cases: it is what
    stops ordinary prose from being decoded into noise and scanned.

    Returns the decoded bytes, or None if this is not base64 in either alphabet.
    """
    padded = token + "=" * (-len(token) % 4)
    for altchars in (None, b"-_"):
        try:
            return base64.b64decode(padded, altchars=altchars, validate=True)
        # nosec B112 - the swallow IS the logic. "Not valid base64 in this alphabet"
        # is the ordinary answer for most candidates, and the only way to ask is to
        # try the decode. A candidate that decodes in neither alphabet returns None
        # below and is dropped, so nothing is silently passed through.
        except Exception:  # noqa: BLE001  # nosec B112 - see above
            continue
    return None


def normalize_and_desmuggle(text: str) -> str:
    """Normalizes Unicode NFKC and strips zero-width/invisible characters used for smuggling."""
    if not text:
        return text
    normalized = unicodedata.normalize("NFKC", text)
    return INVISIBLE_CHARS_PATTERN.sub("", normalized)


def calculate_shannon_entropy(text: str) -> float:
    """Calculates Shannon entropy in bits per character.

    Formula: H(S) = - sum(p(x) * log2(p(x)))

    Time Complexity: O(N) where N is the length of text.
    Space Complexity: O(U) where U is the number of unique characters.

    Args:
        text: Input string token.

    Returns:
        Shannon entropy in bits per character.
    """
    if not text:
        return 0.0

    length = len(text)
    counts = Counter(text)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)

    return entropy


# Keys whose subtrees redact_payload already walks by shape. Deep redaction skips
# them so a value is never redacted twice; redacting a synthetic placeholder would
# mint a second token and rehydration would restore the placeholder, not the original.
_TARGETED_PAYLOAD_KEYS: frozenset[str] = frozenset({"messages", "prompt", "system", "input", "instructions"})


class UnmappedBlobError(ValueError):
    """A blob was found in a field no policy claims, under UNMAPPED_BLOB_POLICY=block.

    Carries the JSON path so the operator can add it to `payload_skip_keys` rather
    than guessing which field tripped.
    """

    def __init__(self, json_path: str, size_bytes: int) -> None:
        self.json_path = json_path
        self.size_bytes = size_bytes
        super().__init__(f"Unmapped blob at {json_path} ({size_bytes} bytes)")


def _policy_skip_keys() -> frozenset[str]:
    """Keys the active virtual key's policy claims, so deep redaction leaves them alone."""
    policy = request_policy_ctx.get() or {}
    declared = policy.get("payload_skip_keys")
    if isinstance(declared, str):
        declared = [part.strip() for part in declared.split(",")]
    if not isinstance(declared, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(key) for key in declared if str(key).strip())


class PIIEngine:
    """3-Tier Cascade PII Redaction and Secret Neutralization Engine.

    - Tier 1: Microsecond regex for structured identifiers.
    - Tier 2: Shannon Entropy filter for high-entropy secrets and keys.
    - Tier 3: Contextual Named Entity Recognition via a loaded ONNX model.
    """

    def __init__(
        self,
        enable_tier2: bool = True,
        enable_tier3: bool = True,
        entropy_threshold: Optional[float] = None,
    ) -> None:
        self.enable_tier2: bool = enable_tier2
        self.enable_tier3: bool = enable_tier3
        self.entropy_threshold: float = (
            entropy_threshold if entropy_threshold is not None else settings.SHANNON_ENTROPY_THRESHOLD
        )
        self._onnx_session: Optional[Any] = None
        self._tokenizer: Optional[Any] = None
        self._compiled_profiles: Dict[str, CompiledProfile] = {}
        self._tenant_mappings: Dict[str, str] = {}
        self._global_strict_profile: CompiledProfile = CompiledProfile(name="global_strict")

        self._init_onnx_model()
        self._init_custom_regex()

    def _init_onnx_model(self) -> None:
        """Lazy-loads ONNX runtime session and Tokenizer if configured and available."""
        if not (self.enable_tier3 and settings.ENABLE_TIER3_ONNX_NER and settings.ONNX_MODEL_PATH):
            return

        try:
            import os

            import onnxruntime as ort  # type: ignore
            from tokenizers import Tokenizer  # type: ignore

            self._onnx_session = ort.InferenceSession(
                settings.ONNX_MODEL_PATH,
                providers=["CPUExecutionProvider"],
            )

            model_dir = os.path.dirname(settings.ONNX_MODEL_PATH)
            tokenizer_path = os.path.join(model_dir, "tokenizer.json")
            if os.path.exists(tokenizer_path):
                self._tokenizer = Tokenizer.from_file(tokenizer_path)
            else:
                logger.warning("ONNX tokenizer.json not found in model directory. Tier 3 will fallback to regex.")
                self._tokenizer = None
        except Exception as exc:
            logger.error("Failed to initialize ONNX NER pipeline: %s", exc)
            self._onnx_session = None
            self._tokenizer = None

    def _init_custom_regex(self) -> None:
        """Loads and compiles BYOR (Bring Your Own Regex) patterns via re2 and builds Policy Profiles."""
        self._compiled_profiles.clear()
        self._tenant_mappings.clear()

        all_tier1 = list(TIER1_PATTERNS)
        all_tier3 = set(TIER3_NER_ENTITIES)

        if settings.CUSTOM_REGEX_PATH and os.path.exists(settings.CUSTOM_REGEX_PATH):
            if re2 is None:
                logger.error(
                    "google-re2 is required for BYOR custom regex to prevent ReDoS. Skipping custom regex load."
                )
            else:
                try:
                    with open(settings.CUSTOM_REGEX_PATH, "r", encoding="utf-8") as f:
                        yaml_data = yaml.safe_load(f) or {}

                    config = CustomRegexConfig(**yaml_data)

                    for custom_pattern in config.custom_patterns:
                        # Compile with RE2 to avoid backtracking-based regular-expression behavior.
                        compiled = re2.compile(custom_pattern.pattern)
                        all_tier1.append((custom_pattern.name, compiled))

                    tier1_lookup = {name: pattern for name, pattern in all_tier1}

                    for profile_config in config.profiles:
                        profile = CompiledProfile(name=profile_config.name)
                        for t1_name in profile_config.tier1_regex:
                            if t1_name in tier1_lookup:
                                profile.tier1_patterns.append((t1_name, tier1_lookup[t1_name]))
                        profile.tier3_ner_entities = set(profile_config.tier2_ner)
                        self._compiled_profiles[profile.name] = profile

                    self._tenant_mappings = config.tenant_mappings

                    logger.info(
                        "Successfully loaded custom regex patterns and %d profiles from %s",
                        len(self._compiled_profiles),
                        settings.CUSTOM_REGEX_PATH,
                    )
                except Exception as exc:
                    logger.error("Failed to load custom regex configuration: %s", exc)

        self._global_strict_profile = CompiledProfile(
            name="global_strict", tier1_patterns=all_tier1, tier3_ner_entities=all_tier3
        )

        # Fires at construction and on every policy hot-reload.
        self._warn_if_ner_is_declared_but_unbacked()

    @property
    def name_redaction_active(self) -> bool:
        """True only when a Tier 3 NER model is actually loaded and usable."""
        return bool(self.enable_tier3 and self._onnx_session and self._tokenizer)

    def describe_ner_coverage(self) -> Dict[str, Any]:
        """Report whether name (PERSON) redaction is actually in force, and for whom."""
        expecting = sorted(
            profile.name
            for profile in (
                list(self._compiled_profiles.values()) + [self._global_strict_profile]
            )
            if profile.tier3_ner_entities
        )
        active = self.name_redaction_active
        return {
            "name_redaction_active": active,
            "model_loaded": bool(self._onnx_session and self._tokenizer),
            "tier3_enabled": bool(self.enable_tier3),
            "model_path": settings.ONNX_MODEL_PATH or None,
            "declared_entities": sorted(TIER3_NER_ENTITIES),
            "profiles_expecting_ner": expecting,
            "unbacked_profiles": [] if active else expecting,
            "reason": None
            if active
            else (
                "no ONNX NER model is loaded; there is no heuristic fallback, so no "
                "PERSON spans are produced for any profile"
            ),
        }

    def _warn_if_ner_is_declared_but_unbacked(self) -> None:
        """Log once per (re)compile if a profile expects PERSON and no model can supply it."""
        coverage = self.describe_ner_coverage()
        if coverage["name_redaction_active"] or not coverage["unbacked_profiles"]:
            return
        logger.warning(NER_DISABLED_WARNING)

    def get_profile(self, virtual_key_id: str) -> CompiledProfile:
        """Retrieves active profile for the given tenant virtual_key_id in O(1) time."""
        profile_name = self._tenant_mappings.get(virtual_key_id)
        if profile_name:
            return self._compiled_profiles.get(profile_name, self._global_strict_profile)
        return self._global_strict_profile

    def detect_spans(
        self, text: str, active_profile: Optional[CompiledProfile] = None
    ) -> List[Tuple[int, int, str, str]]:
        """Returns entity spans detected by the enabled 3-tier cascade.

        Time Complexity: O(N * P) where N is text length and P is pattern count.
        Space Complexity: O(K) where K is number of matched spans.

        Args:
            text: Input raw string to analyze.
            active_profile: The compiled policy profile for the current tenant.

        Returns:
            List of non-overlapping spans: (start_index, end_index, entity_type, matched_text)
        """
        if not text:
            return []

        raw_spans: List[Tuple[int, int, str, str]] = []

        if active_profile is None:
            active_profile = self._global_strict_profile

        # Locate encoded bodies once.
        base64_candidates: List[Tuple[int, int, str]] = []
        excluded_interiors: List[Tuple[int, int]] = []
        for match in BASE64_CANDIDATE_PATTERN.finditer(text):
            start, end = match.span()
            if end - start > MAX_BASE64_INSPECTION_CHARS:
                interior_start = start + BASE64_BOUNDARY_SCAN_CHARS
                interior_end = end - BASE64_BOUNDARY_SCAN_CHARS
                if interior_start < interior_end:
                    excluded_interiors.append((interior_start, interior_end))

                # Decode each guard on its own.
                blob = match.group(0)
                tail_offset = len(blob) - BASE64_BOUNDARY_SCAN_CHARS
                tail_offset -= tail_offset % 4
                for guard_start, guard_text in (
                    (start, blob[:BASE64_BOUNDARY_SCAN_CHARS]),
                    (start + tail_offset, blob[tail_offset:]),
                ):
                    if len(guard_text) >= 8:
                        base64_candidates.append(
                            (guard_start, guard_start + len(guard_text), guard_text)
                        )
                continue
            base64_candidates.append((start, end, match.group(0)))

        scan_segments: List[Tuple[int, str]] = []
        if excluded_interiors:
            cursor = 0
            for start, end in excluded_interiors:
                if cursor < start:
                    scan_segments.append((cursor, text[cursor:start]))
                cursor = max(cursor, end)
            if cursor < len(text):
                scan_segments.append((cursor, text[cursor:]))
        else:
            scan_segments.append((0, text))

        # Tier 1: Structured DFA Regex Scanning (including Tier 1.5 Custom Patterns)
        with tracer.start_as_current_span("regex_tier"):
            for entity_type, pattern in active_profile.tier1_patterns:
                for offset, segment in scan_segments:
                    for match in pattern.finditer(segment):
                        matched_text = match.group(0)
                        # classify_tier1_match is deliberately NOT called here.
                        raw_spans.append(
                            (offset + match.start(), offset + match.end(), entity_type, matched_text)
                        )

        # Tier 1b: the same patterns over a confusables-folded copy.
        if not text.isascii():
            folded = text.translate(_CONFUSABLE_TRANSLATION)
            if len(folded) == len(text) and folded != text:
                for offset, segment in scan_segments:
                    folded_segment = folded[offset:offset + len(segment)]
                    for entity_type, pattern in active_profile.tier1_patterns:
                        for match in pattern.finditer(folded_segment):
                            start = offset + match.start()
                            end = offset + match.end()
                            raw_spans.append((start, end, entity_type, text[start:end]))

        # Tier 2: Shannon Entropy Analysis (Detects unformatted API keys, hashes, secret tokens)
        if self.enable_tier2 and settings.ENABLE_TIER2_ENTROPY:
            for offset, segment in scan_segments:
                for match in CANDIDATE_SECRET_PATTERN.finditer(segment):
                    token = match.group(0)
                    if len(token) >= settings.SHANNON_MIN_LENGTH:
                        entropy = calculate_shannon_entropy(token)
                        is_hex = all(c in "0123456789abcdefABCDEF" for c in token)
                        # Standard Base64/alphanumeric secrets (>= 4.5 bits) or high-entropy Hex tokens (>= 3.4 bits on >= 24 chars)
                        if entropy >= self.entropy_threshold or (
                            is_hex and len(token) >= 24 and entropy >= 3.4
                        ):
                            raw_spans.append(
                                (
                                    offset + match.start(),
                                    offset + match.end(),
                                    "SECRET_KEY",
                                    token,
                                )
                            )

        # Obfuscated Base64 Candidate Inspection
        for start, end, token in base64_candidates:
            probe = token
            for _ in range(MAX_BASE64_DECODE_DEPTH):
                decoded_bytes = _decode_base64_candidate(probe)
                if decoded_bytes is None:
                    break
                decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
                if len(decoded_text) < 6:
                    break

                if any(
                    pattern.search(decoded_text)
                    for _entity_type, pattern in active_profile.tier1_patterns
                ):
                    # The span stays the whole source run, as it was.
                    raw_spans.append((start, end, "BASE64_OBFUSCATED_PII", token))
                    break

                # Nothing found.
                nested = decoded_text.strip()
                if not BASE64_CANDIDATE_PATTERN.fullmatch(nested):
                    break
                probe = nested

        # Obfuscated Percent-Encoded Candidate Inspection.
        for run in PERCENT_RUN_PATTERN.finditer(text):
            token = run.group(0)
            # Two C-level rejections before any Python work.
            if "%" not in token or not PERCENT_ESCAPE_PATTERN.search(token):
                continue
            start, end = run.span()
            if end - start > MAX_PERCENT_INSPECTION_CHARS:
                # Decode the edges only.
                probes = (
                    token[:PERCENT_BOUNDARY_SCAN_CHARS],
                    token[-PERCENT_BOUNDARY_SCAN_CHARS:],
                )
            else:
                probes = (token,)
            for probe in probes:
                try:
                    decoded_text = unquote(probe, errors="ignore")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Percent candidate decode failed: %s", exc)
                    continue
                # Nothing actually decoded, so Tier 1 already saw this text as-is.
                if decoded_text == probe or len(decoded_text) < 6:
                    continue
                if any(
                    pattern.search(decoded_text)
                    for _entity_type, pattern in active_profile.tier1_patterns
                ):
                    raw_spans.append((start, end, "PERCENT_OBFUSCATED_PII", token))
                    break

        # Obfuscated HTML-Entity Candidate Inspection.
        run_end = -1
        for entity in HTML_ENTITY_PATTERN.finditer(text):
            if entity.start() < run_end:
                continue  # already inside a run this loop captured
            start = entity.start()
            while start > 0 and text[start - 1] not in _ENTITY_RUN_DELIMITERS:
                start -= 1
            end = entity.end()
            while end < len(text) and text[end] not in _ENTITY_RUN_DELIMITERS:
                end += 1
            run_end = end
            token = text[start:end]
            if end - start > MAX_ENTITY_INSPECTION_CHARS:
                # Decode the edges only, rather than skipping the run.
                probes = (
                    token[:ENTITY_BOUNDARY_SCAN_CHARS],
                    token[-ENTITY_BOUNDARY_SCAN_CHARS:],
                )
            else:
                probes = (token,)
            for probe in probes:
                try:
                    decoded_text = html.unescape(probe)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("HTML entity candidate decode failed: %s", exc)
                    continue
                # Nothing actually decoded, so Tier 1 already saw this text as-is.
                if decoded_text == probe or len(decoded_text) < 6:
                    continue
                if any(
                    pattern.search(decoded_text)
                    for _entity_type, pattern in active_profile.tier1_patterns
                ):
                    raw_spans.append((start, end, "ENTITY_OBFUSCATED_PII", token))
                    break

        # Tier 3: Contextual Named Entity Recognition (Person, Location, Org).
        # Model-backed only. With no session loaded this block does nothing and no PERSON
        # span is produced; see the comment block above TIER3_NER_ENTITIES for why there
        # is no regex fallback, and describe_ner_coverage() for how the gap is surfaced.
        if self.enable_tier3 and self._onnx_session and self._tokenizer:
            with tracer.start_as_current_span("onnx_tier"):
                try:
                    import numpy as np  # type: ignore

                    encoded = self._tokenizer.encode(text)
                    input_ids = np.array([encoded.ids], dtype=np.int64)
                    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)

                    ort_inputs = {
                        self._onnx_session.get_inputs()[0].name: input_ids,
                        self._onnx_session.get_inputs()[1].name: attention_mask,
                    }
                    logits = self._onnx_session.run(None, ort_inputs)[0]
                    predictions = np.argmax(logits, axis=2)[0]

                    current_entity = None
                    current_start = -1

                    for idx, pred_id in enumerate(predictions):
                        # Simplified label parsing (assuming id > 0 means a named entity for now)
                        if pred_id > 0:
                            if current_entity is None:
                                current_entity = "PERSON"
                                current_start = idx
                        else:
                            if current_entity is not None:
                                offsets = encoded.offsets
                                if current_start < len(offsets) and idx - 1 < len(offsets):
                                    start_char = offsets[current_start][0]
                                    end_char = offsets[idx - 1][1]
                                    if start_char < end_char:
                                        match_text = text[start_char:end_char]
                                        if current_entity in active_profile.tier3_ner_entities:
                                            raw_spans.append(
                                                (start_char, end_char, current_entity, match_text)
                                            )
                                current_entity = None
                except Exception as exc:
                    # There is nothing to fall back to, and that is the point. A failed
                    # inference means name redaction did not happen for this request; say
                    # so at warning level rather than substituting a heuristic that would
                    # make the gap invisible.
                    logger.warning(
                        "Tier 3 ONNX inference failed; NO name (PERSON) spans were produced "
                        "for this request and none were approximated: %s",
                        exc,
                    )

        # Deduplicate and resolve overlapping spans (prioritize earliest start, then longest span)
        raw_spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        non_overlapping: List[Tuple[int, int, str, str]] = []
        last_end = -1

        for span in raw_spans:
            start, end, entity_type, matched_text = span
            if start >= last_end:
                non_overlapping.append(span)
                last_end = end

        # A span that ends on a digit while the run continues is a partial match.
        completed: List[Tuple[int, int, str, str]] = []
        for index, (start, end, entity_type, matched_text) in enumerate(non_overlapping):
            if end > start and text[end - 1].isdigit():
                limit = non_overlapping[index + 1][0] if index + 1 < len(non_overlapping) else len(text)
                grown = _extend_span_over_digit_run(text, end, limit)
                if grown != end:
                    end, matched_text = grown, text[start:grown]
            completed.append((start, end, entity_type, matched_text))

        return completed

    def redact_text(self, text: str, vault: Vault, active_profile: Optional[CompiledProfile] = None) -> str:
        """Redacts PII spans in text and registers deterministic mappings in the Vault.

        Time Complexity: O(N + K) where N is text length and K is number of matches.
        Space Complexity: O(N) for reconstructed redacted text.

        Args:
            text: Input string to redact.
            vault: Session-scoped Vault to store mappings.
            active_profile: The compiled policy profile for the current tenant.

        Returns:
            Redacted text containing placeholders or synthetic replacements.
        """
        if not text:
            return text

        # De-smuggle zero-width Unicode characters and normalize NFKC
        working_text = normalize_and_desmuggle(text)

        spans = self.detect_spans(working_text, active_profile)
        if not spans:
            return working_text

        # Replace spans from right to left to preserve preceding string indices
        result = list(working_text)
        for start, end, entity_type, matched_text in reversed(spans):
            token = vault.get_or_create_token(matched_text, entity_type)
            result[start:end] = list(token)

        return "".join(result)

    def redact_payload(
        self,
        payload: Dict[str, Any],
        vault: Vault,
        active_profile: Optional[CompiledProfile] = None,
        depth: int = 0,
        max_depth: int = 20,
    ) -> Dict[str, Any]:
        """Recursively traverses LLM payload dictionary and redacts string content.

        Args:
            payload: Request JSON dictionary.
            vault: Session-scoped Vault.
            active_profile: The compiled policy profile for the current tenant.
            depth: Current traversal recursion depth.
            max_depth: Maximum permitted JSON nesting depth before raising ValueError.

        Returns:
            A deep-redacted copy of the request payload.
        """
        if depth > max_depth:
            raise ValueError("Maximum payload nesting depth exceeded")

        if not isinstance(payload, dict):
            return payload

        new_payload = payload.copy()

        # Redact OpenAI / Anthropic messages array
        if "messages" in new_payload and isinstance(new_payload["messages"], list):
            redacted_messages = []
            for msg in new_payload["messages"]:
                if isinstance(msg, dict):
                    if "messages" in msg:
                        redacted_messages.append(
                            self.redact_payload(msg, vault, active_profile, depth=depth + 1, max_depth=max_depth)
                        )
                        continue

                    msg_copy = msg.copy()
                    role = msg_copy.get("role", "")

                    # 1. Redact message content (string or multi-part content blocks)
                    if "content" in msg_copy and isinstance(msg_copy["content"], str):
                        content_str = msg_copy["content"]
                        # Indirect Prompt Injection Defense in tool responses
                        if role in ("tool", "function"):
                            content_str = INDIRECT_PROMPT_INJECTION_PATTERN.sub(
                                "[SYSTEM_OVERRIDE_BLOCKED]", content_str
                            )
                        msg_copy["content"] = self.redact_text(content_str, vault, active_profile)
                    elif "content" in msg_copy and isinstance(msg_copy["content"], list):
                        new_content_blocks = []
                        for block in msg_copy["content"]:
                            if isinstance(block, dict):
                                block_copy = block.copy()
                                if "text" in block_copy and isinstance(block_copy["text"], str):
                                    text_val = block_copy["text"]
                                    if role in ("tool", "function"):
                                        text_val = INDIRECT_PROMPT_INJECTION_PATTERN.sub(
                                            "[SYSTEM_OVERRIDE_BLOCKED]", text_val
                                        )
                                    block_copy["text"] = self.redact_text(text_val, vault, active_profile)
                                # An Anthropic tool_result nests its own content, as a
                                # string or as further blocks.
                                if "content" in block_copy:
                                    block_copy["content"] = self._redact_nested_content(
                                        block_copy["content"], vault, active_profile
                                    )
                                new_content_blocks.append(block_copy)
                            else:
                                new_content_blocks.append(block)
                        msg_copy["content"] = new_content_blocks

                    # 2. Redact message participant name if present
                    if "name" in msg_copy and isinstance(msg_copy["name"], str):
                        raw_name = msg_copy["name"]
                        spaced_name = raw_name.replace("_", " ")
                        redacted_spaced = self.redact_text(spaced_name, vault, active_profile)
                        if redacted_spaced != spaced_name:
                            msg_copy["name"] = redacted_spaced.replace(" ", "_")
                        elif raw_name and raw_name[0].isupper():
                            msg_copy["name"] = vault.get_or_create_token(raw_name, "PERSON").replace(" ", "_")

                    # 3. Redact OpenAI tool_calls function arguments in multi-turn agent history
                    if "tool_calls" in msg_copy and isinstance(msg_copy["tool_calls"], list):
                        new_tool_calls = []
                        for tc in msg_copy["tool_calls"]:
                            if isinstance(tc, dict):
                                tc_copy = tc.copy()
                                if "function" in tc_copy and isinstance(tc_copy["function"], dict):
                                    fn_copy = tc_copy["function"].copy()
                                    if "arguments" in fn_copy and isinstance(fn_copy["arguments"], str):
                                        fn_copy["arguments"] = self.redact_text(
                                            fn_copy["arguments"], vault, active_profile
                                        )
                                    tc_copy["function"] = fn_copy
                                new_tool_calls.append(tc_copy)
                            else:
                                new_tool_calls.append(tc)
                        msg_copy["tool_calls"] = new_tool_calls

                    # 4. Redact legacy OpenAI function_call
                    if "function_call" in msg_copy and isinstance(msg_copy["function_call"], dict):
                        fn_copy = msg_copy["function_call"].copy()
                        if "arguments" in fn_copy and isinstance(fn_copy["arguments"], str):
                            fn_copy["arguments"] = self.redact_text(fn_copy["arguments"], vault, active_profile)
                        msg_copy["function_call"] = fn_copy

                    redacted_messages.append(msg_copy)
                else:
                    redacted_messages.append(msg)
            new_payload["messages"] = redacted_messages

        # Redact legacy OpenAI prompt field
        if "prompt" in new_payload:
            if isinstance(new_payload["prompt"], str):
                new_payload["prompt"] = self.redact_text(new_payload["prompt"], vault, active_profile)
            elif isinstance(new_payload["prompt"], list):
                new_payload["prompt"] = [
                    self.redact_text(p, vault, active_profile) if isinstance(p, str) else p
                    for p in new_payload["prompt"]
                ]

        # Redact system prompt if separated at top level.
        if "system" in new_payload:
            if isinstance(new_payload["system"], str):
                new_payload["system"] = self.redact_text(new_payload["system"], vault, active_profile)
            elif isinstance(new_payload["system"], list):
                new_payload["system"] = self._redact_text_blocks(
                    new_payload["system"], vault, active_profile
                )

        # Redact the Responses API instructions field.
        if "instructions" in new_payload and isinstance(new_payload["instructions"], str):
            new_payload["instructions"] = self.redact_text(
                new_payload["instructions"], vault, active_profile
            )

        # Redact embeddings / moderation / responses input field
        if "input" in new_payload:
            if isinstance(new_payload["input"], str):
                new_payload["input"] = self.redact_text(new_payload["input"], vault, active_profile)
            elif isinstance(new_payload["input"], list):
                new_payload["input"] = [
                    self.redact_text(item, vault, active_profile)
                    if isinstance(item, str)
                    else self._redact_input_item(item, vault, active_profile)
                    for item in new_payload["input"]
                ]

        # Everything else still reaches the provider verbatim. Walk those too.
        if settings.ENABLE_DEEP_PAYLOAD_REDACTION:
            protected = settings.payload_protected_keys_set | _policy_skip_keys()
            ceiling = settings.PAYLOAD_MAX_REDACT_STRING_LENGTH
            for key in list(new_payload):
                if key in _TARGETED_PAYLOAD_KEYS or key in protected:
                    continue
                new_payload[key] = self._deep_redact(
                    new_payload[key], vault, active_profile, protected, ceiling, depth + 1, max_depth, key
                )

        return new_payload

    def _deep_redact(
        self,
        node: Any,
        vault: Vault,
        active_profile: Optional[CompiledProfile],
        protected: frozenset[str],
        max_string_length: int,
        depth: int,
        max_depth: int,
        json_path: str = "",
    ) -> Any:
        """Redacts every string beneath `node`, skipping structure and opaque blobs."""
        if depth > max_depth:
            raise ValueError("Maximum payload nesting depth exceeded")

        if isinstance(node, str):
            if len(node) > max_string_length or node.startswith("data:"):
                return self._handle_unmapped_blob(node, json_path, active_profile)
            return self.redact_text(node, vault, active_profile)

        if isinstance(node, dict):
            return {
                key: (
                    value
                    if key in protected
                    else self._deep_redact(
                        value,
                        vault,
                        active_profile,
                        protected,
                        max_string_length,
                        depth + 1,
                        max_depth,
                        f"{json_path}.{key}" if json_path else key,
                    )
                )
                for key, value in node.items()
            }

        if isinstance(node, list):
            return [
                self._deep_redact(
                    item,
                    vault,
                    active_profile,
                    protected,
                    max_string_length,
                    depth + 1,
                    max_depth,
                    f"{json_path}[{index}]",
                )
                for index, item in enumerate(node)
            ]

        return node

    def _handle_unmapped_blob(
        self,
        blob: str,
        json_path: str,
        active_profile: Optional[CompiledProfile] = None,
    ) -> str:
        """Applies UNMAPPED_BLOB_POLICY to a blob in a field no policy claims."""
        policy = settings.UNMAPPED_BLOB_POLICY
        if policy == "block":
            raise UnmappedBlobError(json_path or "<root>", len(blob))

        # `skip` is an explicit opt-out.
        if policy == "skip" or blob.startswith("data:"):
            return blob

        # The tail offset is aligned back to the blob's OWN 4-character framing.
        tail_offset = len(blob) - BLOB_BOUNDARY_SCAN_CHARS
        tail_offset -= tail_offset % 4
        # Joined with a newline so a match cannot straddle the seam.
        probe = blob[:BLOB_BOUNDARY_SCAN_CHARS] + "\n" + blob[tail_offset:]

        try:
            edge_scan = "pii_found" if self.detect_spans(probe, active_profile) else "clean"
        except Exception:  # noqa: BLE001  # nosec B110
            # A probe failure must not take down the request.
            logger.warning("Unmapped-blob edge scan failed at %s", json_path or "<root>")
            edge_scan = "failed"

        AuditLogger.log_unmapped_blob(
            json_path=json_path or "<root>",
            size_bytes=len(blob),
            # Different events for telemetry.
            edge_scan=edge_scan,
        )

        if edge_scan == "pii_found":
            # A FIXED marker, not a vault token.
            return "[UNMAPPED_BLOB_PII_REDACTED]"

        return blob

    def _redact_nested_content(
        self,
        content: Any,
        vault: Vault,
        active_profile: Optional[CompiledProfile] = None,
        max_depth: int = 8,
    ) -> Any:
        """Redacts a tool_result's own content, a string or further blocks."""
        if isinstance(content, str):
            return self.redact_text(content, vault, active_profile)
        if not isinstance(content, list):
            return content

        root: List[Any] = [block.copy() if isinstance(block, dict) else block for block in content]
        pending: List[Tuple[List[Any], int]] = [(root, 0)]
        cursor = 0
        while cursor < len(pending):
            blocks, depth = pending[cursor]
            cursor += 1
            for position, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                if isinstance(block.get("text"), str):
                    block["text"] = self.redact_text(block["text"], vault, active_profile)
                nested = block.get("content")
                if isinstance(nested, str):
                    block["content"] = self.redact_text(nested, vault, active_profile)
                elif isinstance(nested, list) and depth + 1 < max_depth:
                    copied = [item.copy() if isinstance(item, dict) else item for item in nested]
                    block["content"] = copied
                    pending.append((copied, depth + 1))
                blocks[position] = block
        return root

    def _redact_text_blocks(
        self,
        blocks: List[Any],
        vault: Vault,
        active_profile: Optional[CompiledProfile] = None,
    ) -> List[Any]:
        """Redacts the `text` of every content block, leaving other block types alone."""
        redacted: List[Any] = []
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block_copy = block.copy()
                block_copy["text"] = self.redact_text(block_copy["text"], vault, active_profile)
                redacted.append(block_copy)
            else:
                redacted.append(block)
        return redacted

    def _redact_input_item(
        self,
        item: Any,
        vault: Vault,
        active_profile: Optional[CompiledProfile] = None,
    ) -> Any:
        """Redacts one Responses API input item."""
        if not isinstance(item, dict):
            return item

        item_copy = item.copy()
        content = item_copy.get("content")
        if isinstance(content, str):
            item_copy["content"] = self.redact_text(content, vault, active_profile)
        elif isinstance(content, list):
            item_copy["content"] = self._redact_text_blocks(content, vault, active_profile)

        for tool_field in ("arguments", "output"):
            if isinstance(item_copy.get(tool_field), str):
                item_copy[tool_field] = self.redact_text(item_copy[tool_field], vault, active_profile)

        return item_copy


pii_engine = PIIEngine()
