# Open WebUI, LangChain, and LlamaIndex Recipe

The repository provides [smoke clients and integration examples](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/examples/integrations) for common frameworks.

## Routing Configuration

Configure your framework to use the proxy's OpenAI-compatible endpoint:
- Docker network: `http://llm-shield:8000/v1`
- Local host: `http://localhost:8000/v1`

### Open WebUI
Set the `OPENAI_API_BASE_URL` environment variable to the proxy address. Set the provider API key to a client credential accepted by the proxy.

*Note: Open WebUI uses `/v1/models`. If your upstream LLM provider does not support this endpoint, you must configure a hardcoded model allowlist in Open WebUI.*

### LangChain
When instantiating `ChatOpenAI`, set the `base_url` parameter to the proxy address.

### LlamaIndex
When using the OpenAI adapter, set the `api_base` parameter to the proxy address.

## Validation Checklist
Changing the base URL is just the first step. You must verify framework-specific behavior:
- Chat streaming (SSE)
- Tool/Function calling
- Structured outputs (JSON mode)
- Retry and error handling mechanisms
- Non-chat routes (RAG, audio, image endpoints require separate evaluation)
