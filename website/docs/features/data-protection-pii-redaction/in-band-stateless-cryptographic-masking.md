# In-Band Stateless Cryptographic Masking

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does

`STATELESS_CRYPTO` mode replaces detected sensitive values with an AES-256-GCM encrypted token rather than storing a plaintext mapping in a Redis database. The resulting token contains a random nonce, authenticated ciphertext, and a tag encoded in URL-safe Base64. It does not contain the encryption key.

This mode removes the need for an external Redis dependency for supported masking flows. However, it is important to note that ciphertext derived from protected data is still sent to the configured upstream LLM, and any party with access to the encryption key can recover the plaintext.

## How It Works (Request and Response Flow)

1. **Detection:** The engine identifies a configured sensitive value.
2. **Encryption:** The proxy encrypts the value using the configured 32-byte `SHIELD_ENCRYPTION_KEY` and emits an `[ENC_v1_...]` token in the request payload.
3. **Egress:** The transformed request is forwarded to the upstream LLM.
4. **Rehydration:** If the upstream model returns the intact token in its response, the proxy decrypts the token on the fly and replaces it with the original value before sending the response back to the client.

*Note: Models are not guaranteed to echo tokens intact. A model might omit, split, or alter the token. Always test your specific model and prompt patterns before relying heavily on stateless rehydration.*

## Security Properties & Limitations

- **Authentication:** AES-GCM authenticates both the ciphertext and the associated data. Modified or malformed tokens will fail decryption.
- **Key Management:** Security heavily depends on key entropy, nonce uniqueness, secure key rotation, and strict access controls. 
- **Rotation Risk:** The standard implementation uses a single active key. If you rotate the key, any older in-flight tokens (from delayed model responses) will fail to decrypt. Key rotation must be coordinated with request draining.
- **Compliance:** While removing Redis eliminates a datastore attack surface, it does not automatically establish compliance or eliminate data liability.

## Configuration

Set the masking mode to `STATELESS_CRYPTO` and provide a valid 32-byte Base64 or hex-encoded key:

```env
SHIELD_DEFAULT_MASKING_MODE=STATELESS_CRYPTO
SHIELD_ENCRYPTION_KEY=<your_32_byte_key>
```
*Note: Always load `SHIELD_ENCRYPTION_KEY` through a secure secret-management mechanism (e.g., AWS Secrets Manager, HashiCorp Vault). Do not commit it to source control.*

If you are running multiple proxy replicas, all instances must share the same key material to successfully decrypt tokens in a load-balanced environment.

## Related Implementation & Tests

- `llm_shield_proxy/engines/crypto_vault.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/crypto.py`
- `tests/test_stateless_crypto.py`
- `tests/engines/stateless_mutation_engine/`
