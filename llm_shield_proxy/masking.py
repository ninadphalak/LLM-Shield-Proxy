"""Enterprise Masking Mode definitions."""

from enum import Enum
from typing import Optional

from llm_shield_proxy.config import settings

class MaskingMode(str, Enum):
    """Supported masking modes for the proxy."""
    SYNTHETIC = "SYNTHETIC"
    STRUCTURAL_TAG = "STRUCTURAL_TAG"
    SCRUB = "SCRUB"
    STATELESS_CRYPTO = "STATELESS_CRYPTO"

def resolve_masking_mode(header_value: Optional[str] = None) -> MaskingMode:
    """Resolves masking mode from header or default configuration."""
    if header_value:
        try:
            return MaskingMode(header_value.upper())
        except ValueError:
            pass
    try:
        return MaskingMode(settings.SHIELD_DEFAULT_MASKING_MODE.upper())
    except ValueError:
        return MaskingMode.SYNTHETIC
