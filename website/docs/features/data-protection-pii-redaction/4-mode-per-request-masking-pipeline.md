# 4-Mode Per-Request Masking Pipeline

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **4-Mode Per-Request Masking Pipeline** allows an authorized request to select one of four masking representations via an HTTP header. This selection dictates *how* detected sensitive values are represented in the outgoing request, not *which* values are detected. Administrators can override this header via policy.

## How It Works
The proxy reads the `X-Shield-Masking-Mode` header and stores the allowed setting in a `ContextVar` for the duration of the request.

The available modes are:
1. **`SYNTHETIC` (Default):** Replaces detected entities with deterministic, format-aware substitutes (e.g., synthetic SSNs or names). This retains syntax for downstream validation, though task quality remains workload-dependent.
2. **`STRUCTURAL_TAG`:** Replaces entities with explicit, bracketed placeholders (e.g., `[EMAIL_1]`). This is ideal for legacy compliance pipelines or explicit regex auditing.
3. **`SCRUB`:** Replaces a detected value with `***` and does not create a rehydration mapping. It acts as a one-way redaction. 
4. **`STATELESS_CRYPTO`:** Secures data in-transit using AES-256-GCM envelope encryption directly within the payload, bypassing the need for Redis storage.

```mermaid
flowchart TD
    A[Client Request + Header] --> B(Check X-Shield-Masking-Mode)
    B -->|SYNTHETIC| C[John -> Michael]
    B -->|STRUCTURAL_TAG| D[John -> PERSON_1]
    B -->|SCRUB| E[John -> ***]
    B -->|STATELESS_CRYPTO| F[John -> enc_9f...]
```

## Configuration Flags

| Header / Override | Description | Linked Guide |
| :--- | :--- | :--- |
| `X-Shield-Masking-Mode` | HTTP header to dynamically set the mode (`SYNTHETIC`, `STRUCTURAL_TAG`, `SCRUB`, `STATELESS_CRYPTO`). | [View in POLICIES.md](/docs/policies) |
| `SHIELD_DEFAULT_MASKING_MODE` | The global default mode if the client does not specify the header. A role can set its own. | [View in deployment.md](/docs/deployment) |
| `enforced_masking_mode` (role, `policies.yaml`) | Pins the role to one mode. The header is ignored. An unknown value fails closed to `SCRUB`. | [View in POLICIES.md](/docs/policies) |
| `allowed_masking_modes` (role, `policies.yaml`) | List of modes a client may pick with the header. A header outside the list is rejected with `403`; with no header the role's default applies. | [View in POLICIES.md](/docs/policies) |

Resolution order: the role's `enforced_masking_mode`, then the header (checked against `allowed_masking_modes` when set), then `SHIELD_DEFAULT_MASKING_MODE`. The same rules apply on the proxy and on the `/v1/guard/*` API.

```yaml
roles:
  compliance_scrub:
    enforced_masking_mode: SCRUB        # header ignored
  partner:
    SHIELD_DEFAULT_MASKING_MODE: SCRUB
    allowed_masking_modes: [SCRUB, HMAC] # SYNTHETIC/STRUCTURAL_TAG/STATELESS_CRYPTO -> 403
```

## Implementation Details & Edge Cases
* **Request Isolation:** Mode overrides use `contextvars.ContextVar` for explicit context propagation. Concurrency tests cover task creation, background work, and exception paths to prevent context bleed.
* **Vault Behavior:** `STATELESS_CRYPTO` encrypts data in place and does not utilize the Redis TTL Vault for the payload.

## FAQ

**Q: Does allowing clients to set this header bypass security?**
A: The header does not control which entities are detected, but the representation matters: `SYNTHETIC`, `STRUCTURAL_TAG` and `STATELESS_CRYPTO` keep the value recoverable, while `SCRUB` retains nothing. Use the role's `allowed_masking_modes` or `enforced_masking_mode` to stop callers from choosing a mode the role should not use.

**Q: Can I force all clients to use a specific mode and ignore the header?**
A: Yes. Set `enforced_masking_mode` for the specific role in `policies.yaml`. The resolved policy overrides the client's header.

**Q: Why use `SCRUB` if it removes context from the LLM?**
A: `SCRUB` replaces a detected value with a short marker (`***`) and deliberately prevents rehydration. This is useful when the LLM absolutely does not need the original context and you want to ensure the data cannot be recovered on the return path.

## Practical Effect
This feature allows an authorized caller (or centralized policy) to choose between synthetic substitution, structural tags, one-way scrubbing, and cryptographic masking. Entity detection logic and access control operate independently of this choice.

## Related Tests
Tests: [`tests/test_pii_engine.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_pii_engine.py), [`tests/test_audit_2026_10_crypto_guard.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_audit_2026_10_crypto_guard.py).
