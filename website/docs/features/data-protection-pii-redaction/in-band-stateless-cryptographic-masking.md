# In-Band Stateless Cryptographic Masking

[Back to Features Catalog](/docs/features-overview)

## Purpose

`STATELESS_CRYPTO` replaces a detected value with an AES-256-GCM token instead of storing a
plaintext-to-placeholder mapping in Redis. The token contains a random nonce plus authenticated
ciphertext and tag encoded with URL-safe Base64. It does not contain the encryption key.

This mode removes the external mapping-database dependency for supported flows. It does not remove
all data risk: ciphertext derived from protected data is sent to the configured upstream, key
holders can recover the plaintext, and process memory contains plaintext during transformation.

## Request and response flow

1. The detector identifies a configured value.
2. The proxy encrypts the value with the configured 32-byte `SHIELD_ENCRYPTION_KEY` and emits an
   `[ENC_v1_...]` token.
3. The transformed request is handed to the configured upstream client.
4. If the provider returns the intact token, the bounded response path can decrypt and replace it
   for the authorized client.

Provider echo is not assured. A model can omit, split, alter, summarize, or transform a token. Test
the selected provider, model, prompt pattern, and parser before depending on rehydration.

## Security properties and limits

- AES-GCM authenticates ciphertext and associated data used by the selected implementation path.
- A modified or malformed token is not treated as successfully decrypted.
- Security depends on key entropy, access control, nonce uniqueness, implementation correctness,
  rotation, backups, and the surrounding deployment.
- The standard text token format currently uses a single active key. Coordinate key rotation with
  request draining and retained-token lifetime; changing the key can make older in-flight tokens
  unrecoverable.
- Removing Redis avoids one datastore attack surface but does not create "zero data liability" or
  establish compliance.

## Configuration

Set `SHIELD_DEFAULT_MASKING_MODE=STATELESS_CRYPTO` and provide
`SHIELD_ENCRYPTION_KEY` as a valid 32-byte Base64- or hex-encoded value. Load it through the
deployment's secret-management mechanism rather than source control.

All replicas that may handle the same in-flight token need the corresponding key material. Measure
token expansion, model behavior, latency, and process RSS in the intended multi-replica topology.

## Related implementation and tests

- `llm_shield_proxy/engines/crypto_vault.py`
- `llm_shield_proxy/engines/stateless_mutation_engine/crypto.py`
- `tests/test_stateless_crypto.py`
- `tests/engines/stateless_mutation_engine/`
