"""Non-reversible virtual-key fingerprints for logs, metrics and policy lookup."""
import hashlib
import hmac
from functools import lru_cache

_FINGERPRINT_SALT = b"llm_shield_fingerprint_v2"


@lru_cache(maxsize=4096)
def _get_vkid_from_hash(key_hash: str) -> str:
    return hmac.new(_FINGERPRINT_SALT, key_hash.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def get_virtual_key_id(raw_key: str) -> str:
    """HMAC-SHA256 fingerprint of a key, so logs never store plaintext keys."""
    if not raw_key:
        return "anonymous"
    return _get_vkid_from_hash(hashlib.sha256(raw_key.encode("utf-8")).hexdigest())
