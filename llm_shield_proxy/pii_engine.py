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
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from llm_shield_proxy.config import settings
from llm_shield_proxy.vault import Vault
from llm_shield_proxy.config_schema import CustomRegexConfig

import os
import yaml
try:
    import re2
except ImportError:
    re2 = None

logger = logging.getLogger(__name__)

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
        self._custom_patterns: List[Tuple[str, Any]] = []
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
        """Loads and compiles BYOR (Bring Your Own Regex) patterns via re2."""
        if not settings.CUSTOM_REGEX_PATH or not os.path.exists(settings.CUSTOM_REGEX_PATH):
            return

        if re2 is None:
            logger.error("google-re2 is required for BYOR custom regex to prevent ReDoS. Skipping custom regex load.")
            return

        try:
            with open(settings.CUSTOM_REGEX_PATH, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
            
            config = CustomRegexConfig(**yaml_data)
            
            for custom_pattern in config.custom_patterns:
                # Compile using re2 to guarantee O(N) execution and ReDoS immunity
                compiled = re2.compile(custom_pattern.pattern)
                self._custom_patterns.append((custom_pattern.name, compiled))
                
            logger.info("Successfully loaded %d custom regex patterns from %s", len(self._custom_patterns), settings.CUSTOM_REGEX_PATH)
        except Exception as exc:
            logger.error("Failed to load custom regex configuration: %s", exc)

    def detect_spans(self, text: str) -> List[Tuple[int, int, str, str]]:
        """Detects all PII and secret entity spans across the 3-Tier cascade.

        Time Complexity: O(N * P) where N is text length and P is pattern count.
        Space Complexity: O(K) where K is number of matched spans.

        Args:
            text: Input raw string to analyze.

        Returns:
            List of non-overlapping spans: (start_index, end_index, entity_type, matched_text)
        """
        if not text:
            return []

        raw_spans: List[Tuple[int, int, str, str]] = []

        # Tier 1: Structured DFA Regex Scanning
        for entity_type, pattern in TIER1_PATTERNS:
            for match in pattern.finditer(text):
                raw_spans.append((match.start(), match.end(), entity_type, match.group(0)))

        # Tier 1.5: BYOR Custom Regex Scanning (O(N) re2 execution)
        for entity_type, pattern in self._custom_patterns:
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
                    for entity_type, pattern in TIER1_PATTERNS:
                        if pattern.search(decoded_text):
                            raw_spans.append((match.start(), match.end(), "BASE64_OBFUSCATED_PII", token))
                            break
            except Exception as exc:
                logger.debug("Base64 candidate decode failed: %s", exc)

        # Tier 3: Contextual Named Entity Recognition (Person, Location, Org)
        if self.enable_tier3:
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
                                        raw_spans.append((start_char, end_char, current_entity, match_text))
                                current_entity = None
                except Exception as exc:
                    logger.debug("ONNX inference failed, falling back to regex: %s", exc)
                    for entity_type, pattern in TIER3_NER_PATTERNS:
                        for match in pattern.finditer(text):
                            raw_spans.append((match.start(), match.end(), entity_type, match.group(0)))
            else:
                for entity_type, pattern in TIER3_NER_PATTERNS:
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

    def redact_text(self, text: str, vault: Vault) -> str:
        """Redacts PII spans in text and registers deterministic mappings in the Vault.

        Applies Unicode de-smuggling (stripping zero-width and invisible format characters)
        and obfuscated Base64 detection to prevent adversarial filter evasion.

        Time Complexity: O(N + K) where N is text length and K is number of matches.
        Space Complexity: O(N) for reconstructed redacted text.

        Args:
            text: Input string to redact.
            vault: Session-scoped Vault to store mappings.

        Returns:
            Redacted text containing placeholders or synthetic replacements.
        """
        if not text:
            return text

        # De-smuggle zero-width Unicode characters and normalize NFKC
        working_text = normalize_and_desmuggle(text)

        spans = self.detect_spans(working_text)
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
        depth: int = 0,
        max_depth: int = 20,
    ) -> Dict[str, Any]:
        """Recursively traverses LLM payload dictionary and redacts string content.

        Supports standard OpenAI/Anthropic/Gemini payload structures (messages array, prompt, system, input, tool_calls).
        Protects against indirect prompt injection in tool responses and JSON recursion bombs.

        Args:
            payload: Request JSON dictionary.
            vault: Session-scoped Vault.
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
                        redacted_messages.append(self.redact_payload(msg, vault, depth=depth + 1, max_depth=max_depth))
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
                        msg_copy["content"] = self.redact_text(content_str, vault)
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
                                    block_copy["text"] = self.redact_text(text_val, vault)
                                new_content_blocks.append(block_copy)
                            else:
                                new_content_blocks.append(block)
                        msg_copy["content"] = new_content_blocks

                    # 2. Redact message participant name if present
                    if "name" in msg_copy and isinstance(msg_copy["name"], str):
                        raw_name = msg_copy["name"]
                        spaced_name = raw_name.replace("_", " ")
                        redacted_spaced = self.redact_text(spaced_name, vault)
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
                                        fn_copy["arguments"] = self.redact_text(fn_copy["arguments"], vault)
                                    tc_copy["function"] = fn_copy
                                new_tool_calls.append(tc_copy)
                            else:
                                new_tool_calls.append(tc)
                        msg_copy["tool_calls"] = new_tool_calls

                    # 4. Redact legacy OpenAI function_call
                    if "function_call" in msg_copy and isinstance(msg_copy["function_call"], dict):
                        fn_copy = msg_copy["function_call"].copy()
                        if "arguments" in fn_copy and isinstance(fn_copy["arguments"], str):
                            fn_copy["arguments"] = self.redact_text(fn_copy["arguments"], vault)
                        msg_copy["function_call"] = fn_copy

                    redacted_messages.append(msg_copy)
                else:
                    redacted_messages.append(msg)
            new_payload["messages"] = redacted_messages

        # Redact legacy OpenAI prompt field
        if "prompt" in new_payload:
            if isinstance(new_payload["prompt"], str):
                new_payload["prompt"] = self.redact_text(new_payload["prompt"], vault)
            elif isinstance(new_payload["prompt"], list):
                new_payload["prompt"] = [
                    self.redact_text(p, vault) if isinstance(p, str) else p for p in new_payload["prompt"]
                ]

        # Redact system prompt if separated at top level
        if "system" in new_payload and isinstance(new_payload["system"], str):
            new_payload["system"] = self.redact_text(new_payload["system"], vault)

        # Redact embeddings / moderation / responses input field
        if "input" in new_payload:
            if isinstance(new_payload["input"], str):
                new_payload["input"] = self.redact_text(new_payload["input"], vault)
            elif isinstance(new_payload["input"], list):
                new_payload["input"] = [
                    self.redact_text(item, vault) if isinstance(item, str) else item for item in new_payload["input"]
                ]

        return new_payload


pii_engine = PIIEngine()
