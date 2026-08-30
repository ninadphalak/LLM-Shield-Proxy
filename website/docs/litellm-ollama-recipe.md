# How to use LLM-Shield-Proxy with LiteLLM & Ollama in 3 Lines of YAML

If you are running **LiteLLM** for enterprise routing or **Ollama** for local inference, you already have the infrastructure to serve models. But if your team is processing sensitive customer data, medical records, or developer secrets, you need a way to sanitize those prompts before they hit your logs or upstream cloud models.

The integration problem is preserving incremental Server-Sent Events (SSE) while applying local privacy transformations and optional detector models.

Here is how to place **LLM-Shield-Proxy**-a self-hosted FastAPI sidecar with a bounded SSE sliding-window buffer-in front of LiteLLM or Ollama using Docker Compose. Measure RSS and latency for the selected detector tier and workload.

## The Architecture (Zero-Egress Sandwich)
Instead of your application talking directly to LiteLLM/Ollama, you route traffic through LLM-Shield-Proxy. The proxy intercepts the payload, redacts PII using local Regex and ONNX NER, and forwards the clean traffic upstream.

`[Client App] --> [LLM-Shield-Proxy :8000] --> [LiteLLM :4000 / Ollama :11434]`

## The 3 Lines of YAML

If you already have a `docker-compose.yml` file running LiteLLM or Ollama, simply add the LLM-Shield-Proxy service and point its `UPSTREAM_BASE_URL` to your existing container.

```yaml
version: '3.8'
services:
  # Your existing LLM Router / Local Model
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    ports: ["4000:4000"]

  # 1. Add the LLM-Shield-Proxy Sidecar
  llm-shield:
    image: ninadphalak/llm-shield-proxy:latest
    ports: ["8000:8000"]
    environment:
      # 2. Point it to your existing service
      - UPSTREAM_BASE_URL=http://litellm:4000/v1
      # 3. (Optional) Pass your provider API keys through the proxy
      - OPENAI_API_KEY=sk-your-key
```

## Testing the Flow
Now, instead of pointing your applications to port `4000`, point them to port `8000`. The proxy acts as a transparent 1-line base URL drop-in replacement.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "My name is John Doe and my SSN is 000-11-2222. Summarize my account."}],
    "stream": true
  }'
```

**What happens?**
LiteLLM only ever sees: `"My name is [PERSON_1] and my SSN is [SSN_1]. Summarize my account."`
When the response streams back, LLM-Shield-Proxy uses its lookahead buffer to dynamically re-hydrate the placeholders back into the raw stream without breaking your UI latency.

**🔗 View the full architecture and source code on GitHub:** [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy)
