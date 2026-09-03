# Vault Secrets and mTLS

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy can retrieve configuration secrets from HashiCorp Vault at startup and apply Mutual TLS (mTLS) to backend connections. This centralizes secret management but does not eliminate all secret-exposure risks in the deployment environment.

## How It Works
Rather than mounting static, plaintext credentials (like Kubernetes ConfigMaps) that can be easily copied, the proxy fetches secrets dynamically.

1. **Vault Authentication:** At startup, the proxy authenticates to HashiCorp Vault using a Kubernetes Service Account Token or Vault AppRole.
2. **In-Memory Hydration:** Retrieved secrets are kept in process memory and used to configure the proxy (e.g., Redis credentials, API keys). They are not intentionally written to disk.
3. **mTLS Transport:** The proxy can be configured to use client certificates for backend connections (like Redis), verifying both the client and the server.

```mermaid
flowchart TD
    A[Proxy Startup] -->|Authenticate (K8s Token)| B(HashiCorp Vault)
    B -->|Return Secrets & Certs| C[In-Memory Config]
    C --> D(Connect to Redis)
    D -->|mTLS Handshake| E[(Redis Cluster)]
```

## Performance Profile
- **Overhead:** Vault authentication and fetching secrets add latency to the startup sequence. Background lease renewal consumes minor asynchronous CPU cycles.

## Configuration Flags

| Environment Variable | Description | Linked Guide |
| :--- | :--- | :--- |
| `VAULT_ADDR` | The URL of the HashiCorp Vault cluster. | [View in deployment.md](/docs/deployment) |
| `VAULT_AUTH_METHOD` | The authentication mechanism (`kubernetes`, `approle`, `token`). | [View in deployment.md](/docs/deployment) |

## Implementation Details & Edge Cases
* **Dynamic Lease Renewal:** A background task automatically attempts to renew supported Vault leases before they expire. If renewal fails, the proxy will eventually lose access when the lease expires.
* **Startup Failure:** If Vault is unreachable or authentication fails, the proxy will fail to start. Test your deployment's behavior during Vault outages.
* **Memory Exposure:** Secrets are not written to disk by the application, but they reside in memory. Core dumps, swap space, hypervisor snapshots, or compromised host OS access can still expose them.

## FAQ

**Q: Does it support AWS Secrets Manager or Azure Key Vault natively?**
A: HashiCorp Vault is natively supported for advanced dynamic leases. To use AWS/Azure, you must use standard Kubernetes Secrets integrations (like External Secrets Operator) to mount them into the environment, bypassing the native Vault integration.

## Practical Effect
This feature shifts credential management out of static files and into a centralized, auditable Vault instance. You must still secure the proxy's runtime memory and execution environment.

## Related Tests
Tests: [`tests/test_vault_mtls.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_vault_mtls.py).
