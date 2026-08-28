# Centralized Enterprise Secrets & mTLS

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
The **Centralized Enterprise Secrets & mTLS** feature ensures that the proxy integrates seamlessly into high-security enterprise environments (like DoD or Financial Services) without relying on insecure `.env` files. It natively fetches configuration data, API keys, and cryptographic certs directly from HashiCorp Vault.

## How It Works
Storing the `UPSTREAM_API_KEY` or `REDIS_PASSWORD` in a Kubernetes ConfigMap or local disk is a critical security vulnerability.

1. **Vault Authentication:** On startup, the proxy authenticates to HashiCorp Vault using Kubernetes Service Account Tokens or Vault AppRole credentials.
2. **In-Memory Hydration:** It fetches all required API keys, HMAC salts, and Redis credentials directly into ephemeral RAM. These secrets are never written to disk.
3. **mTLS Transport:** For backend connections (like connecting to the Redis cluster), the proxy pulls X.509 client certificates from Vault and enforces Mutual TLS (mTLS) to guarantee the connection cannot be intercepted or spoofed on the internal network.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart TD
    A[Proxy Startup] -->|Authenticate (K8s Token)| B(HashiCorp Vault)
    B -->|Return Secrets & Certs| C[In-Memory Config]
    C --> D{Connect to Redis}
    D -->|mTLS Handshake| E[(Redis Cluster)]
```
-->

View diagram on GitHub mobile 📱 -->
![Enterprise Secrets Architecture](../images/centralized-enterprise-secrets-mtls.svg)

## Performance Profile
- **Execution Speed:** Vault lookups occur only on startup or during a TTL refresh, causing zero latency in the active HTTP request path.
- **Overhead:** Minimal. Vault tokens are cached using a non-blocking asynchronous TTL mechanism.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `VAULT_ADDR` | The URL of the HashiCorp Vault cluster. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |
| `VAULT_AUTH_METHOD` | The auth mechanism (`kubernetes`, `approle`, `token`). | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Dynamic Lease Renewal:** If Vault issues a dynamic secret (like a short-lived PostgreSQL password for audit logs), the proxy spins up a background `asyncio` task to automatically renew the lease before it expires, ensuring zero downtime.
* **Fail-Closed on Auth Error:** If the proxy cannot authenticate to Vault on startup, it will refuse to start and crash the pod, ensuring it never operates in an unconfigured, insecure state.

## FAQ

**Q: Does it support AWS Secrets Manager or Azure Key Vault?**
A: Currently, HashiCorp Vault is the natively supported provider for advanced dynamic leases and PKI (mTLS). However, basic secrets can be injected into the proxy's environment via standard Kubernetes Secrets integrations (like the External Secrets Operator) which bridge AWS/Azure into the pod.


## Plainspeak
This feature guarantees that the proxy never keeps passwords lying around where a hacker could find them.

Normally, apps read their passwords from a simple file saved on the hard drive. If a hacker breaches the drive, they get the passwords. This feature forces the proxy to fetch passwords directly from an ultra-secure central vault (like HashiCorp Vault) directly into its active memory. The passwords are never saved to the hard drive, meaning there's nothing for a hacker to steal if they break in.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_vault_mtls.py`](../../../tests/test_vault_mtls.py).
