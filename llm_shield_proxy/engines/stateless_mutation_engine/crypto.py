import base64
import binascii
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class StatelessPIICipher:
    """
    Stateless AES-256-GCM cipher for PII tokenization.
    Enforces O(1) space complexity and zero DB dependencies.

    With ``virtual_key_id`` (the proxy always passes it) the per-request key is
    HKDF(master, info = vkid + session) and the AAD carries the vkid too, so a
    ciphertext minted for one tenant never decrypts for another, with or without
    an X-Session-ID. Without it the legacy session-only derivation applies.
    """
    def __init__(
        self,
        key: bytes | None = None,
        keys: dict | None = None,
        version: int = 1,
        session_id: str | None = None,
        virtual_key_id: str | None = None,
    ):
        if keys is None:
            if key is None:
                raise ValueError("Must provide either key or keys")
            keys = {version: key}

        self.version = version
        self.aeads = {}
        self._aad_prefix = b""
        info: bytes | None = None
        if virtual_key_id is not None:
            parts = [b"llm-shield/v3-tenant", virtual_key_id.encode("utf-8"), (session_id or "").encode("utf-8")]
            info = b"".join(len(p).to_bytes(4, "big") + p for p in parts)
            self._aad_prefix = info
        elif session_id:
            info = session_id.encode('utf-8')

        for v, k in keys.items():
            if len(k) != 32:
                raise ValueError(f"Key for version {v} must be 32 bytes for AES-256-GCM")

            if info is not None:
                hkdf = HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=b'llm_shield_salt',
                    info=info
                )
                derived_key = hkdf.derive(k)
            else:
                derived_key = k

            self.aeads[v] = AESGCM(derived_key)

        if self.version not in self.aeads:
            raise ValueError(f"Active version {self.version} not in provided keys")

    def encrypt(self, plaintext: str, aad_context: str) -> str:
        """
        Encrypts PII contextually bound by AAD to prevent substitution attacks.
        Binary layout: [KeyVersion (2b, BigEndian)] + [Nonce (12b)] + [Ciphertext] + [AuthTag (16b)]
        """
        nonce = os.urandom(12)
        # AESGCM.encrypt returns ciphertext + 16-byte tag combined
        ct_and_tag = self.aeads[self.version].encrypt(
            nonce, plaintext.encode('utf-8'), self._aad_prefix + aad_context.encode('utf-8')
        )

        # Architect Constraint: struct.pack('>H', version) for precise BigEndian 2-byte formatting
        version_bytes = struct.pack('>H', self.version)

        payload = version_bytes + nonce + ct_and_tag
        return base64.urlsafe_b64encode(payload).decode('ascii')

    def decrypt(self, token: str, aad_context: str) -> str:
        """
        Decrypts PII validating the contextual AAD binding.
        Traps InvalidTag, binascii, and struct errors to prevent ASGI crashes on bit-flipped tokens.
        """
        try:
            payload = base64.urlsafe_b64decode(token.encode('ascii'))

            if len(payload) < 30: # 2 (version) + 12 (nonce) + 16 (tag)
                return "[CORRUPTED]"

            version_bytes = payload[:2]
            nonce = payload[2:14]
            ct_and_tag = payload[14:]

            version = struct.unpack('>H', version_bytes)[0]
            if version not in self.aeads:
                return "[CORRUPTED]"

            pt = self.aeads[version].decrypt(nonce, ct_and_tag, self._aad_prefix + aad_context.encode('utf-8'))
            return pt.decode('utf-8')
        except (InvalidTag, binascii.Error, struct.error, ValueError):
            return "[CORRUPTED]"
