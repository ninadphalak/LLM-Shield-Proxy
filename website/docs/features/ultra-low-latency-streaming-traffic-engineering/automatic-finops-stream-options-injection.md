# Automatic FinOps `stream_options` Injection

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Automatic FinOps `stream_options` Injection** adds the supported usage-request option to eligible streaming payloads. Provider-reported usage can be missing, rejected, delayed, estimated, or priced differently and must be reconciled before chargeback.

## How It Works
By default, the OpenAI API does not return token usage statistics (input/output counts) on streaming requests unless specifically requested.

1. **Transparent Mutation:** When a client initiates a `stream: true` request, the proxy intercepts the JSON body.
2. **FinOps injection:** On the supported OpenAI-style payload path, the proxy sets `stream_options.include_usage` to `true` when metering is enabled. Validate behavior for caller-supplied values and non-OpenAI adapters.
3. **Usage Extraction:** When the final SSE chunk arrives containing the `usage` object, the proxy extracts this data, attaches the specific tenant's Virtual Key ID to it, and ships it asynchronously to the OpenTelemetry / Prometheus chargeback metrics engine.


```mermaid
flowchart LR
    A[Client Request: stream=True] --> B(Proxy Interceptor)
    B --> C[Inject stream_options]
    C --> D[Egress to OpenAI]
    D -.-> E[Final SSE Chunk w/ Usage]
    E --> F[Prometheus Chargeback Meter]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Virtually none. The usage data is routed to a bounded background queue for asynchronous metric publication.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_FINOPS_METERING` | Toggles the automatic injection and metrics collection. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Non-Destructive Execution:** If a developer's application already explicitly provides `stream_options`, the proxy safely merges the dictionary, ensuring `include_usage` is true without overwriting other parameters (like custom stop sequences).
* **Cross-Provider Normalization:** Anthropic Claude streams usage data by default. The proxy normalizes this behavior, ensuring Prometheus metrics receive uniform input/output token counts regardless of whether the target is OpenAI, Gemini, or Claude.

## FAQ

**Q: Why don't I just have my developers add `stream_options` in their code?**
A: Applications can set the option themselves. Central injection can reduce configuration drift, but usage fields can still be missing, rejected, estimated, or interpreted differently by providers.

**Q: Do I get billed for the tokens used by the synthetic/masked names?**
A: Token count depends on the provider tokenizer and substitute. Compare structural, synthetic, scrub, and cryptographic modes on the actual model before making a cost claim.


## Plainspeak
This feature acts as an automatic accountant that tracks exactly how much AI computing power is being used.

For supported provider requests, the proxy adds `stream_options.include_usage=true`. The provider may ignore or reject the option, and reported usage still requires validation before chargeback.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_finops_meter.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_finops_meter.py).
