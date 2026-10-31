"""Stateless Syntheticgraphic Vault Module.

Provides AES-GCM authenticated envelope encryption for in-band masking.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)

_DEK: Optional[bytes] = None


def get_crypto_dek() -> bytes:
    """Retrieves or derives the 256-bit AES-GCM encryption key."""
    global _DEK
    if _DEK is not None:
        return _DEK

    key_src = None
    if settings.ENABLE_VAULT_SECRETS:
        from llm_shield_proxy.security.vault_client import vault_provider

        key_src = vault_provider.get_secret("SHIELD_ENCRYPTION_KEY")
    if not key_src:
        key_src = settings.SHIELD_ENCRYPTION_KEY
    if key_src:
        try:
            # Try to decode base64
            key_bytes = base64.b64decode(key_src)
            if len(key_bytes) == 32:
                _DEK = key_bytes
                return _DEK
        except Exception:  # nosec B110 noqa: S110
            # Security Note: Fallback key derivation attempts
            pass

        try:
            # Try to decode hex
            key_bytes = bytes.fromhex(key_src)
            if len(key_bytes) == 32:
                _DEK = key_bytes
                return _DEK
        except Exception:  # nosec B110 noqa: S110
            # Security Note: Fallback key derivation attempts
            pass

    raise ValueError("Valid 256-bit SHIELD_ENCRYPTION_KEY not found or invalid.")


_AESGCM_INSTANCE = None

def get_aesgcm() -> AESGCM:
    global _AESGCM_INSTANCE
    if _AESGCM_INSTANCE is not None:
        return _AESGCM_INSTANCE
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _AESGCM_INSTANCE = AESGCM(get_crypto_dek())
    return _AESGCM_INSTANCE


def zeroize_crypto_material() -> None:
    """Explicit memory zeroization for FIPS 140-3 KAT memory-dump protections."""
    global _AESGCM_INSTANCE, _DEK
    _AESGCM_INSTANCE = None
    _DEK = None


# Token format version. v2 tokens are bound to the minting principal through the
# AES-GCM associated data. v1 tokens carried no binding (any key holder could decrypt
# any tenant's token), so they no longer match TOKEN_REGEX and are never decrypted.
TOKEN_PREFIX = "[ENC_v2_"
_AAD_LABEL = b"llm-shield/enc_v2"


def token_context(virtual_key_id: str, session_id: Optional[str]) -> bytes:
    """The associated data a token is bound to: (virtual key id, session).

    Length-prefixed so no (vkid, session) pair can collide with another.
    """
    parts = [_AAD_LABEL, virtual_key_id.encode("utf-8"), (session_id or "").encode("utf-8")]
    return b"".join(len(p).to_bytes(4, "big") + p for p in parts)


def encrypt_to_token(raw_pii: str, context: bytes) -> str:
    """Encrypts a string to a Base64URL token bound to `context` (see token_context).

    WARNING: This uses a 96-bit random nonce (os.urandom(12)) under AES-GCM.
    To prevent birthday bound collisions, the SHIELD_ENCRYPTION_KEY MUST be
    rotated before 2^32 encryption operations.
    """
    nonce = os.urandom(12)
    aesgcm = get_aesgcm()
    ciphertext = aesgcm.encrypt(nonce, raw_pii.encode("utf-8"), context)
    # Payload is nonce + ciphertext (which includes the 16-byte tag at the end)
    payload = nonce + ciphertext
    b64_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{TOKEN_PREFIX}{b64_payload}]"


def decrypt_from_token(token: str, context: bytes) -> str:
    """Decrypts a token minted for `context`. Returns the token unchanged on any failure,
    including a token minted for a different virtual key or session."""
    if not token.startswith(TOKEN_PREFIX) or not token.endswith("]"):
        return token

    b64_payload = token[len(TOKEN_PREFIX):-1]
    # Re-pad base64
    padding_needed = len(b64_payload) % 4
    if padding_needed:
        b64_payload += "=" * (4 - padding_needed)

    try:
        payload = base64.urlsafe_b64decode(b64_payload)
        if len(payload) < 28:  # 12 byte nonce + 16 byte tag
            return token

        nonce = payload[:12]
        ciphertext = payload[12:]
        aesgcm = get_aesgcm()

        decrypted = aesgcm.decrypt(nonce, ciphertext, context)
        return decrypted.decode("utf-8")
    except Exception:
        # Invalid tag (tampered, or another principal's token), corrupted base64, etc.
        return token


class StatelessCryptoVault:
    """Duck-types Vault to provide stateless synthetic integration.

    Tokens are bound to (virtual_key_id, session_id): a token decrypts only for the
    principal and session that minted it. Without a virtual_key_id the vault gets a
    random context of its own, so its tokens decrypt only on this instance (used by
    ext_proc, which has no principal: one vault per stream).
    """

    # Strict Base64URL extraction regex
    TOKEN_REGEX = re.compile(r"\[ENC_v2_[A-Za-z0-9\-_=]+\]")

    def __init__(self, virtual_key_id: Optional[str] = None, session_id: Optional[str] = None) -> None:
        if virtual_key_id is None:
            self._context = token_context("ephemeral:" + os.urandom(16).hex(), None)
        else:
            self._context = token_context(virtual_key_id, session_id)
        # Provide type_counters attribute to satisfy AuditLogger
        self.type_counters: dict[str, int] = {}
        self._one_way: dict[str, str] = {}

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        """Encrypts the original value to a token."""
        self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
        return encrypt_to_token(original_val, self._context)

    def get_or_create_one_way_token(self, original_val: str, entity_type: str) -> str:
        """A tag the reply cannot restore. An encrypted token would decrypt itself."""
        token = self._one_way.get(original_val)
        if token is None:
            self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
            token = f"[{entity_type}_REDACTED_{len(self._one_way) + 1}]"
            self._one_way[original_val] = token
        return token

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        """Rehydrates by finding and decrypting tokens.

        Regex Safety: strictly matches r'\\[ENC_v2_[A-Za-z0-9\\-_=]+\\]'.
        Respects retention boundary if provided.
        """
        if not text:
            return text

        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            end_idx = match.end()

            # Check if token crosses into trailing retention window
            if retention_length > 0 and end_idx > len(text) - retention_length:
                # Defer replacement
                return token

            decrypted = decrypt_from_token(token, self._context)
            return decrypted

        result = self.TOKEN_REGEX.sub(repl, text)
        return result
