# Multi-Provider Translators

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Multi-Provider Translators** feature allows client applications to send standard OpenAI-formatted API requests to the proxy, which then translates them on the fly for supported non-OpenAI LLM providers (e.g., Anthropic Claude). It is a structural data adapter, not a universal API contract.

## How It Works
Client applications shouldn't need to rewrite their network and parsing logic when switching backend LLM providers. 

1. **Schema Interception:** The proxy receives standard OpenAI `v1/chat/completions` JSON payloads.
2. **Translation Layer:** Before egress, the payload is translated into the target provider's specific schema (e.g., extracting the `system` role out of the messages array for Anthropic).
3. **SSE Stream Normalization:** As the upstream provider streams its unique format back, the proxy translates these events back into standard OpenAI `choices[0].delta.content` chunks, allowing the downstream application to parse them without errors.

```mermaid
flowchart LR
    A[OpenAI Client SDK] --> B(Proxy Translator Engine)
    B -->|Translates to Target Schema| C[Target API (e.g., Anthropic)]
    C -.->|Target SSE Stream| B
    B -.->|Normalizes to OpenAI SSE| A
```

## Performance Profile
- **Overhead:** Translation logic uses `orjson` for fast serialization, but it still requires CPU cycles and memory allocations on the event loop.

## Configuration Flags

| Environment Variable | Description | Linked Guide |
| :--- | :--- | :--- |
| `UPSTREAM_BASE_URL` | The URL of the target LLM provider (e.g., `https://api.anthropic.com`). | [View in deployment.md](/docs/deployment) |
| `UPSTREAM_API_KEY` | The exact API key for the target upstream provider. | [View in deployment.md](/docs/deployment) |

## Implementation Details & Edge Cases
* **Role Alternation:** Some providers (like Anthropic) enforce strictly alternating `user` and `assistant` roles. The translator automatically collapses consecutive `user` messages into a single block to prevent API `400` errors.
* **Feature Parity:** The proxy adapts a documented subset of fields. Unsupported parameters are stripped, which may change semantics.

## FAQ

**Q: Do I need to change my `langchain` or `openai` Python SDK code?**
A: You only need to point your client's `base_url` to the proxy. However, you must thoroughly test authentication, tools, streaming, and error paths, as structural translation does not eliminate semantic differences between models.

**Q: How does this interact with PII redaction?**
A: The proxy performs inbound PII redaction *before* translation, and outbound rehydration *after* stream normalization. This ensures the redaction cascade always operates on a consistent internal data structure.

## Practical Effect
This feature allows client applications to standardize on the OpenAI API schema while routing traffic to various backend providers. You must still test for semantic differences and unsupported features when switching models.

## Related Tests
Tests: [`tests/test_provider_adapters.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_provider_adapters.py).
