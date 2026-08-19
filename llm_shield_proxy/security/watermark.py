"""Dynamic Canary Watermarking & Steganography Module.

Injects an invisible cryptographic fingerprint into the outgoing LLM text stream
using zero-width unicode characters.
"""

import hashlib
import hmac
import time
from typing import Optional


def get_identity(
    authorization_header: Optional[str],
    x_virtual_key_header: Optional[str],
    client_ip: Optional[str]
) -> str:
    """Resolves identity in order of precedence: Authorization -> X-Virtual-Key -> Client_IP."""
    if authorization_header:
        # We might have "Bearer " prefix, strip it if necessary or just use the token
        auth = authorization_header.replace("Bearer ", "").strip()
        if auth:
            return auth

    if x_virtual_key_header:
        vk = x_virtual_key_header.strip()
        if vk:
            return vk

    if client_ip:
        ip = client_ip.strip()
        if ip:
            return ip

    return "anonymous_client"


def generate_fingerprint(
    secret: str,
    identity: str,
    session_id: str,
    epoch_minute: int
) -> str:
    """Computes a 16-character HMAC-SHA256 fingerprint."""
    message = f"{identity}:{session_id}:{epoch_minute}".encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256
    ).hexdigest()[:16]


def encode_steganography(hex_fingerprint: str) -> str:
    """Maps a hex fingerprint to invisible Unicode zero-width characters."""
    # Convert each hex char to a 4-bit binary string
    binary_str = "".join(f"{int(c, 16):04b}" for c in hex_fingerprint)

    # Map 0 -> \u200B (Zero-Width Space)
    # Map 1 -> \u200C (Zero-Width Non-Joiner)
    invisible_chars = []
    invisible_chars.append("\u200D") # Start delimiter (Zero-Width Joiner)
    for bit in binary_str:
        if bit == "0":
            invisible_chars.append("\u200B")
        else:
            invisible_chars.append("\u200C")
    invisible_chars.append("\u200D") # End delimiter

    return "".join(invisible_chars)


def generate_watermark_text(
    secret: str,
    authorization_header: Optional[str],
    x_virtual_key_header: Optional[str],
    client_ip: Optional[str],
    session_id: str
) -> str:
    """End-to-end generation of the invisible watermark string."""
    identity = get_identity(authorization_header, x_virtual_key_header, client_ip)
    epoch_minute = int(time.time() / 60)
    fingerprint = generate_fingerprint(secret, identity, session_id, epoch_minute)
    return encode_steganography(fingerprint)
