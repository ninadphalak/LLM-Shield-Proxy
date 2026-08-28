import base64
import os

try:
    import tiktoken
except ImportError:
    tiktoken = None
    print("Warning: tiktoken not installed. Token counts will be simulated.")

def simulate_aes_gcm_encryption(data: str) -> str:
    """Simulates the AES-GCM envelope: [KeyVersion 2B] + [Nonce 12B] + [Ciphertext] + [AuthTag 16B]"""
    # 2 bytes key version + 12 bytes nonce + 16 bytes auth tag = 30 bytes overhead
    overhead = os.urandom(30)
    ciphertext = data.encode('utf-8')  # Simulate encryption by just encoding
    envelope = overhead + ciphertext
    return base64.b64encode(envelope).decode('utf-8')

def benchmark_token_bloat():
    print("--- Tokenization Bloat Benchmark ---")

    if tiktoken:
        enc = tiktoken.get_encoding("cl100k_base")
        def tokenize(x):
            return len(enc.encode(x))
    else:
        # Rough heuristic for base64 token bloat (approx 1 token per 3-4 chars)
        def tokenize(x):
            return len(x) // 3

    test_cases = [
        "john.doe@example.com",
        "Alice",
        "+1-555-019-8372",
        "4929-1234-5678-9012"
    ]

    for plaintext in test_cases:
        pt_tokens = tokenize(plaintext)

        # 1. Base64 Encryption Bloat
        encrypted = simulate_aes_gcm_encryption(plaintext)
        ct_tokens = tokenize(encrypted)

        # 2. JSON Schema Augmentation Bloat
        schema_addition = f'"_ctx_hash_{plaintext[:3]}": {{"type": "string"}}'
        schema_tokens = tokenize(schema_addition)

        total_injected_tokens = ct_tokens + schema_tokens
        bloat_factor = total_injected_tokens / max(pt_tokens, 1)

        print(f"\nPlaintext: {plaintext}")
        print(f"Plaintext Tokens: {pt_tokens}")
        print(f"Encrypted Blob (Base64): {encrypted}")
        print(f"Encrypted Tokens: {ct_tokens}")
        print(f"Schema Augmentation Tokens: {schema_tokens}")
        print(f"Total Injected Tokens: {total_injected_tokens}")
        print(f"Token Bloat Factor: {bloat_factor:.2f}x")

if __name__ == "__main__":
    benchmark_token_bloat()
