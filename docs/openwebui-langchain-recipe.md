# Zero-Egress PII Redaction for Open-WebUI & LangChain

If you are building an enterprise chatbot using **Open-WebUI** or an agentic workflow using **LangChain**, passing SOC 2 or HIPAA audits is notoriously difficult.

The biggest bottleneck is the UI. Chat interfaces rely on Server-Sent Events (SSE) to create that real-time "typewriter" effect. Because network protocols are oblivious to semantic boundaries, standard PII proxies routinely fracture redaction placeholders across multiple TCP packets (e.g., sending `[PER` in chunk 1, and `SON_1]` in chunk 2). When this happens, raw bracket tags leak onto the user's screen, ruining the application.

**LLM-Shield-Proxy** solves this by using an asynchronous sliding-window lookahead buffer. It temporarily holds back unclosed structural brackets until the token resolves, re-hydrating the data with sub-millisecond added overhead—all within a <60MB local footprint.

Here is how to integrate it into Open-WebUI and LangChain in seconds.

## Integration 1: Open-WebUI

Because LLM-Shield-Proxy is a universal OpenAPI catch-all proxy, Open-WebUI treats it exactly like a standard OpenAI endpoint.

1. Spin up the proxy locally on port `8000`.
2. Open your `docker-compose.yml` for Open-WebUI (or your `.env` file).
3. Override the default OpenAI base URL to point to the proxy:

```env
OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1
OPENAI_API_KEY=sk-proxy-local  # The proxy will swap this for your real upstream key
```

Now, any time an employee pastes sensitive customer data into the Open-WebUI chat window, the proxy intercepts it, strips the PII locally, and routes the clean payload to OpenAI/vLLM.

## Integration 2: LangChain (Python)

If you are building LangChain execution loops, you do not need to import heavy PII-scrubbing chains or rewrite your agent logic. Keep your application logic clean and handle compliance at the network layer.

Just modify the `base_url` parameter in your `ChatOpenAI` instantiation:

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# Point LangChain to your local LLM-Shield-Proxy container
chat = ChatOpenAI(
    base_url="http://localhost:8000",  # The Proxy URL
    api_key="sk-proxy-local",
    model="gpt-4o",
    streaming=True,
)

# Send a prompt containing PII
messages = [HumanMessage(content="Schedule a meeting for Sarah Connor at sarah.c@sky.net")]

# The proxy streams the response back with zero latency penalties
for chunk in chat.stream(messages):
    print(chunk.content, end="", flush=True)
```

By decoupling the redaction logic from LangChain, your agents run faster, your Python environment stays lightweight (no `spaCy` or `PyTorch` required in the app container), and your CISO gets a deterministic, zero-egress security boundary.

**🔗 Star the project and view the benchmarks on GitHub:** [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy)
