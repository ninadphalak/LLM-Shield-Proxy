# Service Mesh Native gRPC ext_proc Integration

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **gRPC `ext_proc` integration** lets an Envoy filter send supported request and response body
messages to LLM-Shield-Proxy for processing. This is an alternative to the standalone HTTP reverse
proxy path. It adds gRPC, serialization, scheduling, and mutation work that must be measured.

## How It Works
Routing traffic out of a service mesh to an external HTTP proxy and back adds redundant TCP handshakes and serialization latency.

1. **Envoy delegation:** A configured Envoy `ext_proc` filter sends supported messages to the
   proxy over gRPC, optionally through a Unix socket.
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
- **Overhead:** Envoy owns the upstream connection on this path. The `ext_proc` exchange still adds
  gRPC messages, body buffering or streaming, serialization, and proxy processing.

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


## Practical effect
Envoy can call the proxy as an external processor instead of routing the application through the
proxy's HTTP endpoint. This changes where the proxy sits in the request path; it does not make the
security checks free. Test body modes, timeouts, failure policy, streaming, and latency with the
exact Envoy configuration.

## Related Tests
Tests: [`tests/test_grpc_ext_proc.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_grpc_ext_proc.py).
