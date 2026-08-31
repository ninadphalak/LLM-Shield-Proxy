# Open WebUI, LangChain, and LlamaIndex recipe

The repository provides an [Open WebUI Compose example and Python smoke clients](/docs/integrations)
for LangChain and LlamaIndex.

Use this base URL for the OpenAI-compatible path:

```text
http://llm-shield:8000/v1     # from the same Compose network
http://localhost:8000/v1      # from the host
```

For Open WebUI, set `OPENAI_API_BASE_URL` and a client credential accepted by the shield.
Open WebUI queries `/v1/models`; if the selected upstream does not implement that endpoint,
configure an explicit model allowlist in Open WebUI and test chat separately. RAG, audio, image,
and background-task endpoints are distinct paths and require their own privacy and compatibility
assessment.

For LangChain `ChatOpenAI`, use `base_url`. For LlamaIndex's OpenAI adapter, use `api_base`.
Changing a base URL is only the first step: test the exact messages, tools, structured output,
streaming, retry, and error behavior used by the application.

[Review the runnable examples and acceptance checks](/docs/integrations), then
[apply to the 30-day design-partner pilot](/docs/design-partner-pilot) if your team can run an
independent evaluation.
