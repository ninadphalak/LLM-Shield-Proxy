"""Enterprise Masking Mode definitions."""

import hashlib
import hmac
import logging
from enum import Enum
from typing import Any, Optional

from llm_shield_proxy.core.config import request_policy_ctx, settings

logger = logging.getLogger(__name__)


class MaskingMode(str, Enum):
    """Supported masking modes for the proxy."""

    SYNTHETIC = "SYNTHETIC"
    STRUCTURAL_TAG = "STRUCTURAL_TAG"
    SCRUB = "SCRUB"
    STATELESS_CRYPTO = "STATELESS_CRYPTO"
    HMAC = "HMAC"


class MaskingModeNotAllowed(ValueError):
    """The client asked for a masking mode its role's allowed_masking_modes excludes."""


def _role_value(*names: str) -> Any:
    """A key of the caller's resolved role (policies.yaml), in either spelling."""
    role = request_policy_ctx.get() or {}
    for name in names:
        if name in role and role[name] is not None:
            return role[name]
    return None


def resolve_masking_mode(header_value: Optional[str] = None) -> MaskingMode:
    """Resolves the masking mode for this request.

    Precedence:
      1. The role's ``enforced_masking_mode``: the header is ignored. An invalid value
         fails closed to SCRUB, which retains nothing.
      2. The ``X-Shield-Masking-Mode`` header. When the role sets
         ``allowed_masking_modes``, a header outside that list raises
         MaskingModeNotAllowed (the caller answers 403).
      3. ``SHIELD_DEFAULT_MASKING_MODE`` (role-scoped or global).
    """
    enforced = _role_value("enforced_masking_mode", "ENFORCED_MASKING_MODE")
    if enforced:
        try:
            return MaskingMode(str(enforced).upper())
        except ValueError:
            logger.error("Role enforced_masking_mode is not a known mode; using SCRUB")
            return MaskingMode.SCRUB

    if header_value:
        try:
            requested = MaskingMode(header_value.upper())
        except ValueError:
            requested = None
        if requested is not None:
            allowed = _role_value("allowed_masking_modes", "ALLOWED_MASKING_MODES")
            if allowed is not None:
                if isinstance(allowed, str):
                    allowed = allowed.split(",")
                allowed_set = {str(m).strip().upper() for m in allowed}
                if requested.value not in allowed_set:
                    raise MaskingModeNotAllowed(requested.value)
            return requested

    # Respect legacy boolean flag if explicitly set to False in tests/env
    enable_synthetic = settings.ENABLE_SYNTHETIC_SWAPPING
    default_mode_str = settings.SHIELD_DEFAULT_MASKING_MODE.upper()

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

    # Nothing this vault mints is ever restored.
    get_or_create_one_way_token = get_or_create_token

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        return text


class HmacVault:
    """Deterministic HMAC-SHA256 scrubbing vault."""

    def __init__(self) -> None:
        self.type_counters: dict[str, int] = {}

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
        secret = settings.SHIELD_WATERMARK_SECRET
        if not secret:
            raise ValueError("SHIELD_WATERMARK_SECRET is required for HMAC vault")
        hashed = hmac.new(secret.encode("utf-8"), original_val.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"[{entity_type}_{hashed}]"

    # Nothing this vault mints is ever restored.
    get_or_create_one_way_token = get_or_create_token

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        return text


def masking_mode_not_allowed_response() -> Any:
    """The 403 for a header outside the role's allowed_masking_modes."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "message": "X-Shield-Masking-Mode is not allowed for this key's role.",
                "type": "security_error",
            }
        },
    )
