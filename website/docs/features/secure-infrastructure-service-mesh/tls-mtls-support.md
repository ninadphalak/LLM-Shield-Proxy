# TLS and mTLS Configuration

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy supports inbound TLS termination (HTTPS), inbound client-certificate verification (mTLS), and outbound mTLS to upstream providers or enterprise API gateways.

## How It Works

### Inbound TLS (Server-Side)
To run the proxy over HTTPS, provide a server certificate and private key:
```bash
llm-shield-proxy --tls-cert-file /path/to/server.crt --tls-key-file /path/to/server.key
```

### Inbound Mutual TLS (mTLS)
To require clients to present a trusted certificate, provide a Certificate Authority (CA) bundle:
```bash
llm-shield-proxy \
  --tls-cert-file /path/to/server.crt \
  --tls-key-file /path/to/server.key \
  --client-ca-file /path/to/ca.pem
```
*Note: A valid client certificate authenticates the connection, but application authorization still requires RBAC identity mapping.*

### Outbound TLS (Client-Side)
The proxy verifies upstream provider certificates using system root CAs by default. For air-gapped networks or corporate intercepting proxies, you can supply a custom root CA:
```bash
llm-shield-proxy --ca-bundle-file /path/to/corporate-root-ca.pem
```

For local testing, you can disable verification entirely:
```bash
llm-shield-proxy --insecure-skip-verify
```

### Outbound Mutual TLS (mTLS)
If your upstream API gateway requires the proxy to authenticate itself via mTLS, provide the proxy's client certificate and key via environment variables:
```bash
export OUTBOUND_CLIENT_CERT=/path/to/proxy-client.crt
export OUTBOUND_CLIENT_KEY=/path/to/proxy-client.key
llm-shield-proxy
```

## Practical Effect
These configurations encrypt traffic in transit and cryptographically verify the identities of clients and upstream servers, replacing basic API key authentication with stronger x.509 PKI where required.
