# Automatic FinOps `stream_options` Injection

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Automatic FinOps `stream_options` Injection** ensures that streaming requests return usage data (token counts) by automatically mutating the outbound JSON payload. This enables the proxy to record Prometheus billing metrics even when the client application forgets to request them.

## How It Works
By default, the OpenAI API does not return `prompt_tokens` and `completion_tokens` statistics on SSE streaming requests unless explicitly instructed to do so.

1. **Request Interception:** The proxy detects when an inbound payload requests a stream (`stream: true`).
2. **FinOps Injection:** If FinOps metering is enabled, the proxy injects `stream_options: {"include_usage": true}` into the JSON body before forwarding it upstream. 
3. **Usage Extraction:** When the final SSE event arrives containing the `usage` object, the proxy extracts it and routes it to the asynchronous metrics queue.

```mermaid
flowchart LR
    A[Client Request: stream=True] --> B(Proxy Interceptor)
    B --> C[Inject stream_options]
    C --> D[Egress to OpenAI]
    D -.-> E[Final SSE Chunk w/ Usage]
    E --> F[Prometheus Chargeback Meter]
```

## Performance Profile
- **Overhead:** Mutating the JSON request and parsing the final response chunk requires minor CPU allocations. 

## Configuration Flags

| Environment Variable | Description | Linked Guide |
| :--- | :--- | :--- |
| `ENABLE_FINOPS_METERING` | Toggles the automatic injection and metrics collection. | [View in deployment.md](/docs/deployment) |

## Implementation Details & Edge Cases
* **Non-Destructive Merge:** If the client application already explicitly provides a `stream_options` dictionary, the proxy safely merges the dictionaries to ensure `include_usage` is true without overwriting other user-defined parameters.
* **Anthropic Normalization:** Anthropic Claude returns streaming usage data by default without requiring this injection. The proxy simply normalizes Anthropic's output labels to match the OpenAI standard format.

## FAQ

**Q: Why don't I just have my developers add `stream_options` in their code?**
A: You can. However, relying on client applications leads to configuration drift. Centralizing this injection at the proxy level guarantees consistent telemetry across the entire organization.

**Q: Do I get billed for the tokens used by synthetic/masked PII names?**
A: Token counts are dictated by the upstream provider's tokenizer and the size of the substituted text. You must validate the exact structural differences before asserting cost savings.

## Practical Effect
For supported OpenAI-style requests, the proxy automatically ensures the upstream provider returns token usage statistics, regardless of client configuration.

## Related Tests
Tests: [`tests/test_finops_meter.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_finops_meter.py).
