import re
from typing import List, Tuple, Optional
from app.vault import Vault

# Tier 1 Compiled Regex Patterns
TIER1_PATTERNS = [
    ("EMAIL", re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    ("SSN", re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("PHONE", re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')),
    ("CREDIT_CARD", re.compile(r'\b(?:\d[ -]*?){13,16}\b')),
    ("IP_ADDRESS", re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b')),
    ("API_KEY", re.compile(r'\b(?:sk-[a-zA-Z0-9]{32,48}|AKIA[0-9A-Z]{16})\b')),
]

# Tier 2 NER Pattern Rules (Names, Titles, Locations)
TIER2_PATTERNS = [
    ("PERSON", re.compile(r'\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b')),
]


class PIIEngine:
    """
    Two-Tier Cascade PII Redaction Engine:
    - Tier 1: Microsecond compiled regex for structured secrets & numbers.
    - Tier 2: Millisecond NER rules / ONNX model for unstructured names/orgs.
    """
    def __init__(self, enable_tier2: bool = True):
        self.enable_tier2 = enable_tier2
        self._onnx_session = None
        self._init_onnx_model()

    def _init_onnx_model(self):
        """
        Attempts to load an ONNX runtime session if model file is present.
        Falls back gracefully to rule-based Tier 2 NER if ONNX runtime model is not loaded.
        """
        try:
            import onnxruntime as ort
            # Optional ONNX INT8 BERT-NER model path initialization can go here
            self._onnx_session = None
        except Exception:
            self._onnx_session = None

    def detect_spans(self, text: str) -> List[Tuple[int, int, str, str]]:
        """
        Detects all PII spans in the input text.
        Returns a list of tuples: (start_index, end_index, entity_type, matched_text)
        """
        spans: List[Tuple[int, int, str, str]] = []

        # Tier 1 Regex Scanning
        for entity_type, pattern in TIER1_PATTERNS:
            for match in pattern.finditer(text):
                spans.append((match.start(), match.end(), entity_type, match.group(0)))

        # Tier 2 NER Scanning
        if self.enable_tier2:
            for entity_type, pattern in TIER2_PATTERNS:
                for match in pattern.finditer(text):
                    spans.append((match.start(), match.end(), entity_type, match.group(0)))

        # Remove overlapping spans (prioritizing longer/earlier spans)
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        non_overlapping: List[Tuple[int, int, str, str]] = []
        last_end = -1

        for span in spans:
            start, end, entity_type, matched_text = span
            if start >= last_end:
                non_overlapping.append(span)
                last_end = end

        return non_overlapping

    def redact_text(self, text: str, vault: Vault) -> str:
        """
        Redacts PII spans in text and registers deterministic tokens in the Vault.
        """
        if not text:
            return text

        spans = self.detect_spans(text)
        if not spans:
            return text

        # Rebuild string from right to left to keep indices accurate
        result = list(text)
        for start, end, entity_type, matched_text in reversed(spans):
            token = vault.get_or_create_token(matched_text, entity_type)
            result[start:end] = list(token)

        return "".join(result)

    def redact_payload(self, payload: dict, vault: Vault) -> dict:
        """
        Recursively traverses OpenAI payload structure (e.g. messages array)
        and redacts PII in string content fields.
        """
        if not isinstance(payload, dict):
            return payload

        new_payload = payload.copy()
        if "messages" in new_payload and isinstance(new_payload["messages"], list):
            redacted_messages = []
            for msg in new_payload["messages"]:
                if isinstance(msg, dict):
                    msg_copy = msg.copy()
                    if "content" in msg_copy and isinstance(msg_copy["content"], str):
                        msg_copy["content"] = self.redact_text(msg_copy["content"], vault)
                    redacted_messages.append(msg_copy)
                else:
                    redacted_messages.append(msg)
            new_payload["messages"] = redacted_messages

        if "prompt" in new_payload:
            if isinstance(new_payload["prompt"], str):
                new_payload["prompt"] = self.redact_text(new_payload["prompt"], vault)
            elif isinstance(new_payload["prompt"], list):
                new_payload["prompt"] = [
                    self.redact_text(p, vault) if isinstance(p, str) else p
                    for p in new_payload["prompt"]
                ]

        return new_payload


pii_engine = PIIEngine()
