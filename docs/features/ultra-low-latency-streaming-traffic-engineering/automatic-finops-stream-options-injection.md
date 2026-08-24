# Automatic FinOps `stream_options` Injection

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
**Automatic FinOps `stream_options` Injection** ensures that enterprise chargeback mechanisms have 100% accurate token accounting during Server-Sent Events (SSE) streaming, without requiring developer teams to modify a single line of client application code.

## How It Works
By default, the OpenAI API does not return token usage statistics (input/output counts) on streaming requests unless specifically requested. 

1. **Transparent Mutation:** When a client initiates a `stream: true` request, the proxy intercepts the JSON body.
2. **FinOps Injection:** The proxy automatically injects or ensures the presence of the `stream_options: {"include_usage": true}` parameter in the root payload.
3. **Usage Extraction:** When the final SSE chunk arrives containing the `usage` object, the proxy extracts this data, attaches the specific tenant's Virtual Key ID to it, and ships it asynchronously to the OpenTelemetry / Prometheus chargeback metrics engine.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart LR
    A[Client Request: stream=True] --> B(Proxy Interceptor)
    B --> C[Inject stream_options]
    C --> D[Egress to OpenAI]
    D -.-> E[Final SSE Chunk w/ Usage]
    E --> F[Prometheus Chargeback Meter]
```
-->

View diagram on GitHub mobile 📱 -->
![FinOps Injection Architecture](../images/automatic-finops-stream-options-injection.svg)

## Performance Profile
- **Execution Speed:** Dictionary mutation executes in O(1) time.
- **Overhead:** Virtually none. The usage data is routed to a bounded background queue for asynchronous metric publication.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_FINOPS_METERING` | Toggles the automatic injection and metrics collection. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Non-Destructive Execution:** If a developer's application already explicitly provides `stream_options`, the proxy safely merges the dictionary, ensuring `include_usage` is true without overwriting other parameters (like custom stop sequences).
* **Cross-Provider Normalization:** Anthropic Claude streams usage data by default. The proxy normalizes this behavior, ensuring Prometheus metrics receive uniform input/output token counts regardless of whether the target is OpenAI, Gemini, or Claude.

## FAQ

**Q: Why don't I just have my developers add `stream_options` in their code?**
A: You can! But in large enterprises with hundreds of internal applications and legacy wrappers, auditing every repository to ensure compliance is nearly impossible. Doing it dynamically at the network edge guarantees 100% FinOps observability.

**Q: Do I get billed for the tokens used by the synthetic/masked names?**
A: Yes, but it is vastly cheaper. Standard structural tags `[PERSON_1]` often consume 4 or 5 tokens via BPE. The proxy's synthetic masking ensures that a fake name like "Michael" only consumes 1 token, actively reducing your upstream LLM invoice.
