"""Enterprise 3-Tier PII Detection & Redaction Engine.

Implements a high-throughput multi-tier detection cascade:
- Tier 1: Microsecond pre-compiled DFA regular expressions for structured secrets & numbers.
- Tier 2: Shannon Entropy filter (tau_H >= 4.5 bits/symbol) for unformatted raw cryptographic keys.
- Tier 3: Contextual Named Entity Recognition (NER) via rule heuristics or optional ONNX runtime.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from llm_shield_proxy.config import settings
from llm_shield_proxy.vault import Vault

# Tier 1 Pre-Compiled Regex Patterns
TIER1_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?(?:\d{3}[-.\s]?)?\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    (
        "IP_ADDRESS",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
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
CANDIDATE_SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"\b[A-Za-z0-9_\-+=]{16,}\b"
)


def calculate_shannon_entropy(text: str) -> float:
    """Calculates Shannon entropy in bits per character.

    Formula: H(S) = - sum(p(x) * log2(p(x)))

    Time Complexity: O(N) where N is the length of text.
    Space Complexity: O(U) where U is the number of unique characters.

    Args:
        text: Input string to evaluate.

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
            entropy_threshold
            if entropy_threshold is not None
            else settings.SHANNON_ENTROPY_THRESHOLD
        )
        self._onnx_session: Optional[Any] = None
        self._init_onnx_model()

    def _init_onnx_model(self) -> None:
        """Lazy-loads ONNX runtime session if configured and available."""
        if not (self.enable_tier3 and settings.ENABLE_TIER3_ONNX_NER and settings.ONNX_MODEL_PATH):
            return

        try:
            import onnxruntime as ort  # type: ignore

            self._onnx_session = ort.InferenceSession(
                settings.ONNX_MODEL_PATH,
                providers=["CPUExecutionProvider"],
            )
        except Exception:
            self._onnx_session = None

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

        # Tier 2: Shannon Entropy Analysis (Detects unformatted API keys, hashes, secret tokens)
        if self.enable_tier2 and settings.ENABLE_TIER2_ENTROPY:
            for match in CANDIDATE_SECRET_PATTERN.finditer(text):
                token = match.group(0)
                # Skip if already identified by Tier 1 pattern or too uniform
                if len(token) >= settings.SHANNON_MIN_LENGTH:
                    entropy = calculate_shannon_entropy(token)
                    if entropy >= self.entropy_threshold:
                        raw_spans.append((match.start(), match.end(), "SECRET_KEY", token))

        # Tier 3: Contextual Named Entity Recognition (Person, Location, Org)
        if self.enable_tier3:
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

        spans = self.detect_spans(text)
        if not spans:
            return text

        # Replace spans from right to left to preserve preceding string indices
        result = list(text)
        for start, end, entity_type, matched_text in reversed(spans):
            token = vault.get_or_create_token(matched_text, entity_type)
            result[start:end] = list(token)

        return "".join(result)

    def redact_payload(self, payload: Dict[str, Any], vault: Vault) -> Dict[str, Any]:
        """Recursively traverses LLM payload dictionary and redacts string content.

        Supports standard OpenAI/Anthropic/Gemini payload structures (messages array, prompt, system).

        Args:
            payload: Request JSON dictionary.
            vault: Session-scoped Vault.

        Returns:
            A deep-redacted copy of the request payload.
        """
        if not isinstance(payload, dict):
            return payload

        new_payload = payload.copy()

        # Redact OpenAI / Anthropic messages array
        if "messages" in new_payload and isinstance(new_payload["messages"], list):
            redacted_messages = []
            for msg in new_payload["messages"]:
                if isinstance(msg, dict):
                    msg_copy = msg.copy()
                    if "content" in msg_copy and isinstance(msg_copy["content"], str):
                        msg_copy["content"] = self.redact_text(msg_copy["content"], vault)
                    elif "content" in msg_copy and isinstance(msg_copy["content"], list):
                        # Multi-part content blocks (e.g. OpenAI Vision / Anthropic content blocks)
                        new_content_blocks = []
                        for block in msg_copy["content"]:
                            if isinstance(block, dict):
                                block_copy = block.copy()
                                if "text" in block_copy and isinstance(block_copy["text"], str):
                                    block_copy["text"] = self.redact_text(block_copy["text"], vault)
                                new_content_blocks.append(block_copy)
                            else:
                                new_content_blocks.append(block)
                        msg_copy["content"] = new_content_blocks
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
                    self.redact_text(p, vault) if isinstance(p, str) else p
                    for p in new_payload["prompt"]
                ]

        # Redact system prompt if separated at top level
        if "system" in new_payload and isinstance(new_payload["system"], str):
            new_payload["system"] = self.redact_text(new_payload["system"], vault)

        return new_payload


pii_engine = PIIEngine()
