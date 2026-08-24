from llm_shield_proxy.v3.crypto import StatelessPIICipher


def test_stateless_cipher_key_rotation():
    key_v1 = b"0123456789abcdef0123456789abcdef"
    key_v2 = b"abcdef0123456789abcdef0123456789"

    # Instantiate with v1 as active
    cipher_v1 = StatelessPIICipher(keys={1: key_v1}, version=1)
    pt = "legacy_data"
    token_v1 = cipher_v1.encrypt(pt, "ssn")

    # Instantiate with v2 as active, but providing v1 key for backwards compat
    cipher_v2 = StatelessPIICipher(keys={1: key_v1, 2: key_v2}, version=2)

    # V2 should encrypt using v2 key
    token_v2 = cipher_v2.encrypt("new_data", "ssn")

    # V2 should seamlessly decrypt v1 tokens
    assert cipher_v2.decrypt(token_v1, "ssn") == "legacy_data"

    # V2 should decrypt v2 tokens
    assert cipher_v2.decrypt(token_v2, "ssn") == "new_data"

def test_stateless_cipher_key_rotation_with_hkdf():
    key_v1 = b"0123456789abcdef0123456789abcdef"
    key_v2 = b"abcdef0123456789abcdef0123456789"
    session_id = "tenant_123"

    cipher_v1 = StatelessPIICipher(keys={1: key_v1}, version=1, session_id=session_id)
    token_v1 = cipher_v1.encrypt("legacy_data", "ssn")

    cipher_v2 = StatelessPIICipher(keys={1: key_v1, 2: key_v2}, version=2, session_id=session_id)
    assert cipher_v2.decrypt(token_v1, "ssn") == "legacy_data"
