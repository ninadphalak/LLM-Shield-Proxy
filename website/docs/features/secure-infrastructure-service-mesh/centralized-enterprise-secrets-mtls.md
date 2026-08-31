# Centralized Enterprise Secrets & mTLS

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Centralized Enterprise Secrets & mTLS** feature supports retrieving configured secrets from HashiCorp Vault and applying selected TLS credentials. Suitability for a regulated environment depends on the full deployment, trust, identity, rotation, logging, and operational controls.

## How It Works
Storing the `UPSTREAM_API_KEY` or `REDIS_PASSWORD` in a Kubernetes ConfigMap or local disk is a critical security vulnerability.

1. **Vault Authentication:** On startup, the proxy authenticates to HashiCorp Vault using Kubernetes Service Account Tokens or Vault AppRole credentials.
2. **In-Memory Hydration:** The runtime can fetch configured secrets into process memory without intentionally writing them to an application file. Secret-manager agents, swap, crash dumps, logs, and platform snapshots require separate controls.
3. **mTLS Transport:** Configured backend connections can require client certificates and server verification. Assurance depends on trust roots, hostname validation, key custody, protocol configuration, and the complete connection path.


```mermaid
flowchart TD
    A[Proxy Startup] -->|Authenticate (K8s Token)| B(HashiCorp Vault)
    B -->|Return Secrets & Certs| C[In-Memory Config]
    C --> D(Connect to Redis)
    D -->|mTLS Handshake| E[(Redis Cluster)]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Minimal. Vault tokens are cached using a non-blocking asynchronous TTL mechanism.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `VAULT_ADDR` | The URL of the HashiCorp Vault cluster. | [View in deployment.md](/docs/deployment) |
| `VAULT_AUTH_METHOD` | The auth mechanism (`kubernetes`, `approle`, `token`). | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Dynamic Lease Renewal:** If Vault issues a dynamic secret (like a short-lived PostgreSQL password for audit logs), the proxy spins up a background `asyncio` task to automatically renew the lease before it expires, ensuring zero downtime.
* **Startup failure on configured auth error:** When Vault-backed secrets are required, authentication failure prevents that startup path from becoming ready. Test optional-secret and cached-secret behavior separately.

## FAQ

**Q: Does it support AWS Secrets Manager or Azure Key Vault?**
A: Currently, HashiCorp Vault is the natively supported provider for advanced dynamic leases and PKI (mTLS). However, basic secrets can be injected into the proxy's environment via standard Kubernetes Secrets integrations (like the External Secrets Operator) which bridge AWS/Azure into the pod.


## Plainspeak
This feature centralizes secret retrieval; it does not remove secrets from process memory or every platform persistence and observability path.

Vault-backed retrieval can avoid application-managed plaintext credential files. Operators still need controls for Vault, workload identity, memory, swap, dumps, logs, backups, and administrator access.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_vault_mtls.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_vault_mtls.py).
