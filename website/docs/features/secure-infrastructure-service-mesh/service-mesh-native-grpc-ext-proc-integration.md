# Service Mesh Native gRPC ext_proc Integration

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Service Mesh Native gRPC ext_proc Integration** allows the proxy to operate directly inside modern Kubernetes service meshes (like Istio, Linkerd, or Envoy). Instead of acting as a standalone HTTP reverse proxy, LLM-Shield-Proxy can run as an Envoy External Processing (`ext_proc`) sidecar, intercepting and mutating payloads with near-zero network overhead.

## How It Works
Routing traffic out of a service mesh to an external HTTP proxy and back adds redundant TCP handshakes and serialization latency.

1. **Envoy Delegation:** When an application inside the mesh sends an HTTP request to OpenAI, the Envoy sidecar intercepts it and delegates it to the LLM-Shield-Proxy via a high-speed gRPC stream over a Unix Domain Socket (UDS).
2. **Buffer Mutation:** The proxy receives the raw HTTP body buffers via gRPC, applies the Tier 1/2/3 PII masking, and streams the mutated buffers back to Envoy.
3. **Envoy forwarding:** Envoy forwards the processor's result according to its filter configuration. Applications may observe changed bodies, headers, timing, status, or error behavior and should be integration-tested.


```mermaid
flowchart LR
    A[App Container] --> B(Envoy Sidecar)
    B &lt;-->|gRPC ext_proc over UDS| C(LLM-Shield-Proxy)
    B --> D[Upstream LLM]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Eliminates the need for the proxy to manage outbound TLS/HTTPS connections, offloading that entirely to Envoy.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_EXT_PROC` | Enables the ext_proc gRPC hook alongside the HTTP application lifecycle. | [View in deployment.md](/docs/deployment) |
| `EXT_PROC_SOCK_PATH` | The Unix Domain Socket path (default `/var/run/llm-shield/ext_proc.sock`). | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Streaming Responses:** The `ext_proc` protocol supports bidirectional streaming. The proxy processes Envoy's incoming `ResponseBody` chunks sequentially, applying the SSE Sliding-Window Buffer logic directly to the gRPC messages.
* **Header Manipulation:** The proxy can instruct Envoy to inject the `X-Shield-Attestation` receipts directly into the HTTP headers returning to the client via the gRPC `HeaderMutation` message.

## FAQ

**Q: Can I run this without Istio or Envoy?**
A: Yes. The FastAPI HTTP path and Envoy `ext_proc` path are separate deployment options. Enable and validate only the path the topology uses, including its authentication, failure policy, body modes, and streaming behavior.


## Plainspeak
This feature allows the proxy to operate like a high-speed internal organ of the network, rather than an external checkpoint.

Normally, sending data out to a security proxy and back wastes precious milliseconds. This feature allows the proxy to "plug in" directly to the deep plumbing of an advanced network (a Service Mesh). The data flows straight through it natively without having to leave the fast lane, making the security checks almost entirely invisible to the network speed.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_grpc_ext_proc.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_grpc_ext_proc.py).
