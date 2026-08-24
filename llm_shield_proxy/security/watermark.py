"""Dynamic Canary Watermarking & Steganography Module.

Injects an invisible cryptographic fingerprint into the outgoing LLM text stream
using zero-width unicode characters.
"""

import hashlib
import hmac
import time
from typing import Optional


def get_identity(
    virtual_key_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    authorization_header: Optional[str] = None,
    x_virtual_key_header: Optional[str] = None,
) -> str:
    """Resolves identity safely without leaking raw secret credentials into HMAC oracles."""
    if virtual_key_id and virtual_key_id not in ("BYOK", "anonymous"):
        return virtual_key_id

    if x_virtual_key_header:
        vk = x_virtual_key_header.strip()
        if vk:
            return vk

    if authorization_header:
        auth = authorization_header.replace("Bearer ", "").strip()
        if auth:
            return hashlib.sha256(auth.encode("utf-8")).hexdigest()[:12]

    if client_ip:
        ip = client_ip.strip()
        if ip:
            return ip

    return "anonymous_client"


def generate_fingerprint(secret: str, identity: str, session_id: str, epoch_minute: int) -> str:
    """Computes a 16-character HMAC-SHA256 fingerprint."""
    message = f"{identity}:{session_id}:{epoch_minute}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:16]


def encode_steganography(hex_fingerprint: str) -> str:
    """Maps a hex fingerprint to invisible Unicode zero-width characters."""
    # Convert each hex char to a 4-bit binary string
    binary_str = "".join(f"{int(c, 16):04b}" for c in hex_fingerprint)

    # Map 0 -> \u200B (Zero-Width Space)
    # Map 1 -> \u200C (Zero-Width Non-Joiner)
    invisible_chars = []
    invisible_chars.append("\u200d")  # Start delimiter (Zero-Width Joiner)
    for bit in binary_str:
        if bit == "0":
            invisible_chars.append("\u200b")
        else:
            invisible_chars.append("\u200c")
    invisible_chars.append("\u200d")  # End delimiter

    return "".join(invisible_chars)


def generate_watermark_text(
    secret: str,
    authorization_header: Optional[str] = None,
    x_virtual_key_header: Optional[str] = None,
    client_ip: Optional[str] = None,
    session_id: str = "unknown_session",
    virtual_key_id: Optional[str] = None,
) -> str:
    """End-to-end generation of the invisible watermark string."""
    identity = get_identity(
        virtual_key_id=virtual_key_id,
        client_ip=client_ip,
        authorization_header=authorization_header,
        x_virtual_key_header=x_virtual_key_header,
    )
    epoch_minute = int(time.time() / 60)
    fingerprint = generate_fingerprint(secret, identity, session_id, epoch_minute)
    return encode_steganography(fingerprint)
