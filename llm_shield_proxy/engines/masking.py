"""Enterprise Masking Mode definitions."""

from enum import Enum
from typing import Optional

from llm_shield_proxy.core.config import settings

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
            
    # Respect legacy boolean flag if explicitly set to False in tests/env
    enable_synthetic = getattr(settings, "ENABLE_SYNTHETIC_SWAPPING", True)
    default_mode_str = getattr(settings, "SHIELD_DEFAULT_MASKING_MODE", "SYNTHETIC").upper()
    
    if not enable_synthetic and default_mode_str == "SYNTHETIC":
        return MaskingMode.STRUCTURAL_TAG

    try:
        return MaskingMode(default_mode_str)
    except ValueError:
        return MaskingMode.SYNTHETIC
