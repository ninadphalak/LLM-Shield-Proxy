"""Enterprise Masking Mode definitions."""

from enum import Enum
from typing import Optional
import hashlib
import hmac

from llm_shield_proxy.core.config import settings


class MaskingMode(str, Enum):
    """Supported masking modes for the proxy."""

    SYNTHETIC = "SYNTHETIC"
    STRUCTURAL_TAG = "STRUCTURAL_TAG"
    SCRUB = "SCRUB"
    STATELESS_CRYPTO = "STATELESS_CRYPTO"
    HMAC = "HMAC"


def resolve_masking_mode(header_value: Optional[str] = None) -> MaskingMode:
    """Resolves masking mode from header or default configuration."""
    if header_value:
        try:
            return MaskingMode(header_value.upper())
        except ValueError:
            pass

    # Respect legacy boolean flag if explicitly set to False in tests/env
    enable_synthetic = getattr(settings, "ENABLE_SYNTHETIC_SWAPPING", True)
    default_mode_str = getattr(settings, "SHIELD_DEFAULT_MASKING_MODE", "SYNTHETIC").upper()

    if not enable_synthetic and default_mode_str == "SYNTHETIC":
        return MaskingMode.STRUCTURAL_TAG

    try:
        return MaskingMode(default_mode_str)
    except ValueError:
        return MaskingMode.SYNTHETIC


class ScrubVault:
    """One-way scrubbing vault that redacts entities without retaining plaintext mappings."""

    def __init__(self) -> None:
        self.type_counters: dict[str, int] = {}

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
        return "[REDACTED]"

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        return text


class HmacVault:
    """Deterministic HMAC-SHA256 scrubbing vault."""

    def __init__(self) -> None:
        self.type_counters: dict[str, int] = {}

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
        secret = settings.SHIELD_WATERMARK_SECRET or "default_shield_secret"
        hashed = hmac.new(secret.encode("utf-8"), original_val.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
        return f"[{entity_type}_{hashed}]"

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        return text
