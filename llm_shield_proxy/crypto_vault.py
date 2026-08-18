"""Stateless Cryptographic Vault Module.

Provides AES-GCM authenticated envelope encryption for in-band masking.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from llm_shield_proxy.config import settings

logger = logging.getLogger(__name__)

_DEK: Optional[bytes] = None

def get_crypto_dek() -> bytes:
    """Retrieves or derives the 256-bit AES-GCM encryption key."""
    global _DEK
    if _DEK is not None:
        return _DEK

    key_src = settings.SHIELD_ENCRYPTION_KEY
    if key_src:
        try:
            # Try to decode base64
            key_bytes = base64.b64decode(key_src)
            if len(key_bytes) == 32:
                _DEK = key_bytes
                return _DEK
        except Exception:
            pass
        
        try:
            # Try to decode hex
            key_bytes = bytes.fromhex(key_src)
            if len(key_bytes) == 32:
                _DEK = key_bytes
                return _DEK
        except Exception:
            pass
            
        logger.warning("SHIELD_ENCRYPTION_KEY provided but is not 32 bytes valid base64/hex.")

    # Generate a random process-scoped key if none provided or invalid
    logger.warning("Using ephemeral random AES-GCM key for STATELESS_CRYPTO.")
    _DEK = AESGCM.generate_key(bit_length=256)
    return _DEK

def get_aesgcm() -> AESGCM:
    """Returns a cached AESGCM instance."""
    return AESGCM(get_crypto_dek())

def encrypt_to_token(raw_pii: str) -> str:
    """Encrypts a string to a Base64URL token."""
    nonce = os.urandom(12)
    aesgcm = get_aesgcm()
    ciphertext = aesgcm.encrypt(nonce, raw_pii.encode('utf-8'), None)
    # Payload is nonce + ciphertext (which includes the 16-byte tag at the end)
    payload = nonce + ciphertext
    b64_payload = base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')
    return f"[ENC_v1_{b64_payload}]"

def decrypt_from_token(token: str) -> str:
    """Decrypts a Base64URL token back to string in < 5us. Returns token if fails."""
    # Strict format check based on length/prefix (prefix is 8 chars `[ENC_v1_`)
    if not token.startswith("[ENC_v1_") or not token.endswith("]"):
        return token
        
    b64_payload = token[8:-1]
    # Re-pad base64
    padding_needed = len(b64_payload) % 4
    if padding_needed:
        b64_payload += "=" * (4 - padding_needed)

    try:
        payload = base64.urlsafe_b64decode(b64_payload)
        if len(payload) < 28: # 12 byte nonce + 16 byte tag
            return token
        
        nonce = payload[:12]
        ciphertext = payload[12:]
        aesgcm = get_aesgcm()
        
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted.decode('utf-8')
    except Exception:
        # Invalid tag, corrupted base64, etc.
        return token

class StatelessCryptoVault:
    """Duck-types Vault to provide stateless crypto integration."""
    
    # Strict Base64URL extraction regex
    TOKEN_REGEX = re.compile(r'\[ENC_v1_[A-Za-z0-9\-_=]+\]')

    def __init__(self) -> None:
        # Provide type_counters attribute to satisfy AuditLogger
        self.type_counters: dict[str, int] = {}
        
    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        """Encrypts the original value to a token."""
        self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
        return encrypt_to_token(original_val)
        
    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        """Rehydrates by finding and decrypting tokens.
        
        Regex Safety: strictly matches r'\\[ENC_v1_[A-Za-z0-9\\-_=]+\\]'.
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
                
            decrypted = decrypt_from_token(token)
            return decrypted

        result = self.TOKEN_REGEX.sub(repl, text)
        return result
