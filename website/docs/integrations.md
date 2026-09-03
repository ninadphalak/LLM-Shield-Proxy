# Integration Recipes

LLM-Shield-Proxy provides an OpenAI-compatible reverse-proxy endpoint and a scoped JSON-RPC gateway for MCP.

Below are starting-point configuration recipes for common frameworks.

## Available Examples

| Framework | Implementation Details |
|---|---|
| **LiteLLM** | Configure LiteLLM to point to the proxy via standard `OPENAI_API_BASE` overrides. Validates auth replacement, SSE, and tools. |
| **Open WebUI** | Set `OPENAI_API_BASE_URL` in Open WebUI to point to the proxy. Verified for chat streaming and `/v1/models` passthrough. |
| **LangChain** | Use standard `ChatOpenAI` instantiation with `openai_api_base` set to the proxy. Validates streaming, tools, and structured outputs. |
| **LlamaIndex** | Use the OpenAI LLM adapter with overridden `api_base`. Validates streaming and RAG index calls. |
| **Envoy (`ext_proc`)** | Sample `envoy.yaml` configuring the `ext_proc` gRPC filter to stream HTTP bodies to the proxy over a Unix Domain Socket. |

*You can find the raw configuration files and smoke test scripts in the `examples/integrations/` directory of the repository.*

## MCP Integration Boundary
The `POST /v1/mcp` endpoint supports a strict subset of JSON-RPC methods (`tools/list`, `tools/call`, `resources/read`) required for tool governance.

**Important:** It does *not* implement the full MCP Streamable HTTP transport specification (e.g., capability negotiation, full session management). Do not attempt to point a generic, full-featured MCP SDK client at this endpoint, as initialization handshakes will fail. Use the provided JSON-RPC example client for evaluating tool governance.
