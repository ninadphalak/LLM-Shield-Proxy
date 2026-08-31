# Integration recipes

LLM-Shield-Proxy exposes an OpenAI-compatible reverse-proxy path and a separate scoped JSON-RPC
gateway. The repository includes runnable starting points for common clients and gateways. These
examples establish configuration syntax, not universal compatibility or a security guarantee.

## Available examples

| Integration | Start here | Acceptance checks |
|---|---|---|
| LiteLLM | [Compose and LiteLLM config](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/examples/integrations/litellm) | model alias, auth replacement, SSE, tools, retries |
| Open WebUI | [Compose example](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/examples/integrations/openwebui) | `/v1/models`, chat streaming, task models, optional RAG/audio routes |
| LangChain | [ChatOpenAI smoke client](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/examples/integrations/langchain_chat.py) | messages, streaming, tools, structured output, error mapping |
| LlamaIndex | [OpenAI adapter smoke client](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/examples/integrations/llamaindex_chat.py) | chat, callbacks, streaming, retries, index-specific calls |
| Envoy | [`ext_proc` configuration](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/examples/integrations/envoy/envoy.yaml) | buffer limits, failure policy, timeout, UDS ownership, request/response mutation |
| MCP JSON-RPC | [Scoped gateway client](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/examples/integrations/mcp_jsonrpc.py) | resolver wiring, allowed methods, SSRF policy, JSON/SSE response expectations |

Open WebUI documents `OPENAI_API_BASE_URL` and recommends `/v1/models` plus
`/v1/chat/completions` for its OpenAI-compatible connection. LiteLLM likewise exposes an
OpenAI-style proxy and YAML model configuration. The examples preserve those public contracts
and route them through the shield; optional endpoints are forwarded only when the selected
upstream implements them.

## Minimum integration test

1. Use synthetic identifiers, never real regulated data.
2. Point the shield at a controlled mock upstream.
3. Assert the raw synthetic identifier is absent from the serialized request received by that
   upstream.
4. Exercise streaming with every byte split from the conformance fixtures.
5. Exercise tools, structured output, errors, retries, and cancellation used by the application.
6. Record versions, configuration, detector mode, results, and known unsupported fields.

## MCP boundary

The current `POST /v1/mcp` route supports selected JSON-RPC methods (`tools/list`, `tools/call`,
and `resources/read`). It does **not** currently implement the complete MCP Streamable HTTP
transport: initialization, capability negotiation, sessions, GET/SSE, and other methods remain
outside this route. Use the direct JSON-RPC example for evaluation; do not configure an arbitrary
MCP SDK against it as though it were a conforming Streamable HTTP server.

See [MCP Tool Governance](/docs/guides/mcp-tool-governance) for policy and SSRF configuration,
and [join the 30-day design-partner pilot](/docs/design-partner-pilot) if you can independently
exercise one of these integrations and share public or confidential evaluation evidence.
