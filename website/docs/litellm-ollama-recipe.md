# LiteLLM and Ollama Recipe

You can route an OpenAI-compatible client through LLM-Shield-Proxy to [LiteLLM](https://github.com/BerriAI/litellm). LiteLLM can then route the request to a cloud provider or a local Ollama backend based on the model alias.

**Data Flow:**
```text
client -> LLM-Shield-Proxy (:8000) -> LiteLLM (:4000) -> configured model backend
```

## Integration Steps

1. Review the tested [LiteLLM integration example](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/examples/integrations/litellm) in the repository. It includes a working Docker Compose file and a LiteLLM `config.yaml`.
2. Configure your upstream API keys *only* on the LiteLLM service.
3. Configure the client to authenticate with LLM-Shield-Proxy using a distinct client credential, separating client authentication from upstream provider authentication.
4. Replace the evaluation-only `OVERRIDE_CLIENT_AUTH` proxy setting with your production authentication policy.

## Validation Checklist
Before moving to production, verify the following using synthetic test data:
- Model alias routing (`/v1/models`)
- Chat streaming and tool calls
- Structured output
- Retry and cancellation behavior
- Confirm the final Ollama/provider endpoint receives redacted data.
