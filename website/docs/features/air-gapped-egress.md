# Air-Gapped Egress Gateway Mode

LLM-Shield-Proxy provides an **Air-Gapped Egress Gateway Mode** for environments with strict Zero-Internet corporate subnet architectures. When operating in high-security, highly-regulated enclaves, servers are often denied direct access to the public internet to prevent data exfiltration.

In this mode, the supported upstream path targets the configured internal egress gateway rather than a public provider URL. Enforce and verify the boundary with network policy, DNS, firewall telemetry, and tests for telemetry, updates, model downloads, and error paths.

## Inner Workings & Topology

The intended topology isolates the proxy from direct internet routes; deployment controls must enforce and test that property:

```mermaid
sequenceDiagram
    participant App as Internal App
    participant Shield as LLM-Shield-Proxy
    participant Gateway as Egress Gateway (Internal)
    participant Provider as Public LLM API

    App->>Shield: POST /v1/chat/completions (with sensitive PII)
    Note over Shield: Redact PII payload locally
    Note over Shield: Async DNS resolves internal Gateway IP (ignores SSRF limits)
    Shield->>Gateway: Forward Request (Headers optionally stripped)
    Gateway->>Provider: Forward Request to Public Internet
    Provider-->>Gateway: Streaming SSE Response
    Gateway-->>Shield: Streaming SSE Response
    Note over Shield: Rehydrate PII on-the-fly
    Shield-->>App: Rehydrated Streaming Response
```

## Configuration Flags

The behavior is controlled by several key flags (see [Deployment Configuration](/docs/deployment) for a full reference):

- `AIR_GAPPED_MODE` (bool): Master toggle to enable strict Zero-Internet routing.
- `EGRESS_GATEWAY_URL` (string): The internal proxy URL. Required if `AIR_GAPPED_MODE` is `true`. (e.g., `http://egress-proxy.internal:8080`).
- `FORWARD_CLIENT_AUTH` (bool): Defaults to `false`. On supported forwarding paths, configured client-auth headers are removed before the gateway request is built.

## Critical Logic & Conditional Behaviors

### 1. SSRF Protection Bypass for Internal Gateways
Normally, the LLM-Shield Proxy strictly blocks requests targeting internal or private RFC 1918 IP addresses to prevent Server-Side Request Forgery (SSRF). However, when `AIR_GAPPED_MODE` is active, the `EGRESS_GATEWAY_URL` is parsed by a dedicated, non-blocking async DNS resolver (`_resolve_internal_hostname`) that explicitly *allows* private IPs. The resolved internal IP is substituted into the connection URL so the socket connects to that pinned address rather than re-resolving the hostname at connect time (closing the DNS-rebinding TOCTOU window between the check and the actual connection).

Connecting by resolved IP can fail hostname verification when the certificate contains the DNS name rather than that IP. The proxy separately carries the original `EGRESS_GATEWAY_URL` hostname as an `extensions={"sni_hostname": ...}` override, so the socket uses the validated IP while TLS verifies the configured hostname. The `Host` header is set separately. Exercise this behavior through the selected `httpx`/`httpcore` versions and gateway certificate chain.

### 2. Authorization Header Stripping
For internal mTLS deployments, you might configure your egress gateway to inject the public API keys, effectively treating LLM-Shield as an untrusted internal node that does not have access to the actual LLM API keys.
With `FORWARD_CLIENT_AUTH=False`, the supported forwarding path removes the listed client-provided credential headers. Test aliases, mixed case, duplicate headers, adapters, and alternate routes in the selected deployment.

### 3. Transparent Streaming
Because LLM-Shield manipulates the upstream route via `httpx.AsyncClient`, the core streaming engine remains agnostic to the gateway hop. Standard SSE processing, including the sliding-window de-redaction buffer, functions identically to a direct internet connection.

## Targeted FAQ

**Q: Does Air-Gapped Mode support multiple LLM providers?**
Yes. LLM-Shield routes all upstream paths directly to the `EGRESS_GATEWAY_URL` while preserving the exact pathing (e.g., `/v1/chat/completions`). It is the responsibility of the Egress Gateway to correctly route to the appropriate public LLM.

**Q: Do I need to supply an `UPSTREAM_API_KEY` to LLM-Shield in Air-Gapped Mode?**
No, if `FORWARD_CLIENT_AUTH` is disabled and the egress gateway handles the authentication, LLM-Shield does not need to possess the upstream API keys.

**Q: Can I use this with TLS termination?**
Yes. You can configure `EGRESS_GATEWAY_URL` with `https://` and supply the `SSL_CA_BUNDLE_PATH` so LLM-Shield can verify the egress gateway's internal certificates. Certificate hostname verification is checked against the gateway's real hostname (via the SNI pinning described above), not the resolved IP -- so a normal, domain-issued internal certificate works as expected even though the socket itself connects to the pinned IP.


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_air_gapped_egress.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_air_gapped_egress.py).
