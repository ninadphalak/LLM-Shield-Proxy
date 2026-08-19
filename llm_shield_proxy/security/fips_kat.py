"""FIPS 140-3 Cryptographic Known Answer Tests (KAT) Module.

Ensures the integrity of cryptographic primitives at boot time.
"""

import hashlib
import logging

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

def test_sha256_kat() -> bool:
    """NIST SHA-256 Known Answer Test for empty string."""
    expected_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    h = hashlib.sha256(b"")
    return h.hexdigest() == expected_digest

def test_aes_256_gcm_kat() -> bool:
    """NIST SP 800-38D AES-256-GCM Known Answer Test (Test Case 1)."""
    key = b"\x00" * 32
    iv = b"\x00" * 12
    plaintext = b""
    aad = b""
    expected_tag = bytes.fromhex("530f8afbc74536b9a963b4f1c4cb738b")

    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encryptor.authenticate_additional_data(aad)
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    return ciphertext == b"" and encryptor.tag == expected_tag

def run_fips_kat_self_test() -> bool:
    """Execute all deterministic cryptographic self-tests.

    Returns True if all tests pass. If any fails, raises ValueError or returns False depending on usage.
    For this module, it just returns a boolean.
    """
    try:
        sha_passed = test_sha256_kat()
        if not sha_passed:
            logger.error("FIPS KAT Failure: SHA-256")

        aes_passed = test_aes_256_gcm_kat()
        if not aes_passed:
            logger.error("FIPS KAT Failure: AES-256-GCM")

        return sha_passed and aes_passed
    except Exception as e:
        logger.error(f"FIPS KAT Exception: {e}")
        return False
