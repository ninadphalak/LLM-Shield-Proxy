import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def test_aad_cross_pollination():
    print("--- AAD Cross-Pollination Simulation ---")

    # Simulate generating an ephemeral session key
    session_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(session_key)

    # Two entities in a prompt
    entity_a = b"Alice"
    entity_b = b"Bob"

    # AAD is the JSON-RPC property path to prevent replay
    aad_a = b"parameters.user_1"
    aad_b = b"parameters.user_2"

    # Encrypt
    nonce_a = os.urandom(12)
    nonce_b = os.urandom(12)

    aesgcm.encrypt(nonce_a, entity_a, aad_a)
    ct_b = aesgcm.encrypt(nonce_b, entity_b, aad_b)

    # The LLM receives these blobs in the prompt.
    # Suppose the LLM gets confused (semantic blindness) and places Blob B into user_1's slot.
    print(f"Original AAD A: {aad_a.decode()}")
    print(f"Original AAD B: {aad_b.decode()}")

    print("\nSimulating LLM cross-pollination (Blob B placed in user_1 field):")

    # The proxy tries to decrypt Blob B using the AAD of user_1
    try:
        aesgcm.decrypt(nonce_b, ct_b, aad_a)
        print("Success! (This should not happen)")
    except Exception as e:
        print(f"Decryption failed with exception: {type(e).__name__} - {e}")
        print("Result: GMAC authentication failure. The proxy successfully caught the hallucination/swap, but the tool call data is now unrecoverable, resulting in a deterministic functional failure.")

if __name__ == "__main__":
    test_aad_cross_pollination()
