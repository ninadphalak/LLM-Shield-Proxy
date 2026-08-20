"""Enterprise 3-Tier PII Detection & Redaction Engine.

Implements a high-throughput multi-tier detection cascade:
- Tier 1: Microsecond pre-compiled DFA regular expressions for structured secrets & numbers.
- Tier 2: Shannon Entropy filter (tau_H >= 4.5 bits/symbol) for unformatted raw cryptographic keys.
- Tier 3: Contextual Named Entity Recognition (NER) via rule heuristics or optional ONNX runtime.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.core.config_schema import CustomRegexConfig
from llm_shield_proxy.engines.vault import Vault
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


# Zero-Width, Invisible, and BiDirectional (BiDi/RTL override) Unicode format characters
INVISIBLE_CHARS_PATTERN: re.Pattern[str] = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060-\u2069\uFEFF\u00AD\u180E]")

# Candidate base64 patterns for obfuscated PII smuggling
BASE64_CANDIDATE_PATTERN: re.Pattern[str] = re.compile(r"\b[A-Za-z0-9+/]{20,}={0,2}\b")

# Indirect prompt injection override patterns in tool / retrieval contexts
INDIRECT_PROMPT_INJECTION_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(?:system\s+override|ignore\s+all\s+previous\s+instructions|<\|im_start\|>system|<\|im_end\|>)\b"
)

# Tier 1 Pre-Compiled Regex Patterns
TIER1_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?(?:\d{3}[-.\s]?)?\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    (
        "IP_ADDRESS",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"),
    ),
    (
        "AWS_API_KEY",
        re.compile(r"\b(?:sk-[a-zA-Z0-9]{32,48}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16})\b"),
    ),
    (
        "GITHUB_PAT",
        re.compile(r"\b(?:ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]+)\b"),
    ),
    (
        "SSH_PRIVATE_KEY",
        re.compile(r"-----BEGIN.*?PRIVATE KEY-----", re.DOTALL),
    ),
    (
        "JWT_TOKEN",
        re.compile(r"\bey[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*\b"),
    ),
    ("MRN", re.compile(r"\b\d{3}-\d{2}-\d{2}[A-Za-z0-9]\b")),
]

# Tier 3 Contextual NER Rules (Rule-based fallback for Person/Org names)
TIER3_NER_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "PERSON",
        re.compile(
            r"\b(?:(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})|"
            r"\b(?!(?:Patient|Check|The|A|An|In|On|At|And|Or|But|If|Hey|Can|You|Help|Please|Identify)\b)"
            r"(?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+\b"
        ),
    ),
]

# Candidate pattern for Shannon Entropy evaluation
CANDIDATE_SECRET_PATTERN: re.Pattern[str] = re.compile(r"\b[A-Za-z0-9_\-+=]{16,}\b")


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


class PIIEngine:
    """3-Tier Cascade PII Redaction and Secret Neutralization Engine.

    - Tier 1: Microsecond regex for structured identifiers.
    - Tier 2: Shannon Entropy filter for high-entropy secrets and keys.
    - Tier 3: Contextual Named Entity Recognition via heuristics or optional ONNX model.
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
        all_tier3 = {entity_type for entity_type, _ in TIER3_NER_PATTERNS}

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
                        # Compile using re2 to guarantee O(N) execution and ReDoS immunity
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

    def get_profile(self, virtual_key_id: str) -> CompiledProfile:
        """Retrieves active profile for the given tenant virtual_key_id in O(1) time."""
        profile_name = self._tenant_mappings.get(virtual_key_id)
        if profile_name:
            return self._compiled_profiles.get(profile_name, self._global_strict_profile)
        return self._global_strict_profile

    def detect_spans(
        self, text: str, active_profile: Optional[CompiledProfile] = None
    ) -> List[Tuple[int, int, str, str]]:
        """Detects all PII and secret entity spans across the 3-Tier cascade.

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

        # Tier 1: Structured DFA Regex Scanning (including Tier 1.5 Custom Patterns)
        with tracer.start_as_current_span("regex_tier"):
            for entity_type, pattern in active_profile.tier1_patterns:
                for match in pattern.finditer(text):
                    raw_spans.append((match.start(), match.end(), entity_type, match.group(0)))

        # Tier 2: Shannon Entropy Analysis (Detects unformatted API keys, hashes, secret tokens)
        if self.enable_tier2 and settings.ENABLE_TIER2_ENTROPY:
            for match in CANDIDATE_SECRET_PATTERN.finditer(text):
                token = match.group(0)
                if len(token) >= settings.SHANNON_MIN_LENGTH:
                    entropy = calculate_shannon_entropy(token)
                    is_hex = all(c in "0123456789abcdefABCDEF" for c in token)
                    # Standard Base64/alphanumeric secrets (>= 4.5 bits) or high-entropy Hex tokens (>= 3.4 bits on >= 24 chars)
                    if entropy >= self.entropy_threshold or (is_hex and len(token) >= 24 and entropy >= 3.4):
                        raw_spans.append((match.start(), match.end(), "SECRET_KEY", token))

        # Obfuscated Base64 Candidate Inspection
        for match in BASE64_CANDIDATE_PATTERN.finditer(text):
            token = match.group(0)
            try:
                decoded_bytes = base64.b64decode(token, validate=True)
                decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
                if decoded_text and len(decoded_text) >= 6:
                    for entity_type, pattern in active_profile.tier1_patterns:
                        if pattern.search(decoded_text):
                            raw_spans.append((match.start(), match.end(), "BASE64_OBFUSCATED_PII", token))
                            break
            except Exception as exc:
                logger.debug("Base64 candidate decode failed: %s", exc)

        # Tier 3: Contextual Named Entity Recognition (Person, Location, Org)
        if self.enable_tier3:
            with tracer.start_as_current_span("onnx_tier"):
                if self._onnx_session and self._tokenizer:
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
                                                raw_spans.append((start_char, end_char, current_entity, match_text))
                                    current_entity = None
                    except Exception as exc:
                        logger.debug("ONNX inference failed, falling back to regex: %s", exc)
                        for entity_type, pattern in TIER3_NER_PATTERNS:
                            if entity_type in active_profile.tier3_ner_entities:
                                for match in pattern.finditer(text):
                                    raw_spans.append((match.start(), match.end(), entity_type, match.group(0)))
                else:
                    for entity_type, pattern in TIER3_NER_PATTERNS:
                        if entity_type in active_profile.tier3_ner_entities:
                            for match in pattern.finditer(text):
                                raw_spans.append((match.start(), match.end(), entity_type, match.group(0)))

        # Deduplicate and resolve overlapping spans (prioritize earliest start, then longest span)
        raw_spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        non_overlapping: List[Tuple[int, int, str, str]] = []
        last_end = -1

        for span in raw_spans:
            start, end, entity_type, matched_text = span
            if start >= last_end:
                non_overlapping.append(span)
                last_end = end

        return non_overlapping

    def redact_text(self, text: str, vault: Vault, active_profile: Optional[CompiledProfile] = None) -> str:
        """Redacts PII spans in text and registers deterministic mappings in the Vault.

        Applies Unicode de-smuggling (stripping zero-width and invisible format characters)
        and obfuscated Base64 detection to prevent adversarial filter evasion.

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

        Supports standard OpenAI/Anthropic/Gemini payload structures (messages array, prompt, system, input, tool_calls).
        Protects against indirect prompt injection in tool responses and JSON recursion bombs.

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

        # Redact system prompt if separated at top level
        if "system" in new_payload and isinstance(new_payload["system"], str):
            new_payload["system"] = self.redact_text(new_payload["system"], vault, active_profile)

        # Redact embeddings / moderation / responses input field
        if "input" in new_payload:
            if isinstance(new_payload["input"], str):
                new_payload["input"] = self.redact_text(new_payload["input"], vault, active_profile)
            elif isinstance(new_payload["input"], list):
                new_payload["input"] = [
                    self.redact_text(item, vault, active_profile) if isinstance(item, str) else item
                    for item in new_payload["input"]
                ]

        return new_payload


pii_engine = PIIEngine()
