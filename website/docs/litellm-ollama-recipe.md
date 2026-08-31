# LiteLLM and Ollama recipe

Use the tested [LiteLLM integration example](/docs/integrations) to route an
OpenAI-compatible client through LLM-Shield-Proxy to LiteLLM. LiteLLM can then route the model
alias to a configured cloud or Ollama backend.

```text
client -> LLM-Shield-Proxy :8000 -> LiteLLM :4000 -> configured model backend
```

The example includes a Compose file and LiteLLM `config.yaml`. Set the real provider key only on
the LiteLLM service, keep the shield-to-LiteLLM credential separate from the client credential,
and replace evaluation-only `OVERRIDE_CLIENT_AUTH` with the deployment's authentication policy.

Before describing the stack as supported, test the model alias, `/v1/models`, chat streaming,
tool calls, structured output, cancellation, retries, and the selected Ollama/provider adapter.
Use synthetic values and assert what the controlled upstream actually receives.

[Review the 30-day pilot acceptance criteria](/docs/design-partner-pilot).
