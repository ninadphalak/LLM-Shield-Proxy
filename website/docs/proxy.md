---
slug: /proxy
title: LLM-Shield-Proxy quick start
sidebar_label: Quick start
sidebar_position: 1
description: LLM-Shield-Proxy is a self-hosted proxy for OpenAI-compatible LLM APIs. It replaces personal data and secrets before a request reaches the model provider and restores them in the streamed reply. Try it in a minute with no API key.
keywords: [PII redaction proxy, LLM privacy gateway, OpenAI-compatible proxy, self-hosted, streaming, data masking]
---

# LLM-Shield-Proxy

LLM-Shield-Proxy is a self-hosted proxy for OpenAI-compatible LLM APIs. It replaces the
personal data and secrets it detects (emails, card numbers, SSNs, API keys and more) before a
request goes to the model provider, and puts the original values back into the streamed reply
before your application sees it. Your application changes only its `base_url` and the key it
sends.

## Try it in a minute, with no API key

```bash
pip install llm-shield-proxy

UPSTREAM_BASE_URL=http://127.0.0.1:8765 UPSTREAM_API_KEY=unused VALID_VIRTUAL_KEYS=sk-demo llm-shield-proxy --port 4000 &

pii-leak-benchmark selfcheck --target-base-url http://127.0.0.1:4000/v1 --target-api-key sk-demo
```

The second command starts the proxy. The third sends prompts full of synthetic personal data
through it and plays the model provider, so it sees exactly what the proxy forwarded. It
should print `CLEAN`. On Windows PowerShell, use the
[PowerShell version](conformance/ci.mdx).

## Use it with your application

The proxy needs two keys:

- `VALID_VIRTUAL_KEYS`: the keys your clients send to the proxy. Any other key gets a 401.
- A provider key, here `OPENAI_API_KEY`: what the proxy sends upstream. Your clients never
  hold it.

```bash
export VALID_VIRTUAL_KEYS=sk-my-client-key
export OPENAI_API_KEY=sk-your-openai-key
llm-shield-proxy --host 127.0.0.1 --port 8000
```

```python
from openai import OpenAI

client = OpenAI(api_key="sk-my-client-key", base_url="http://localhost:8000/v1")
```

## Next steps

| You want to | Read |
| :--- | :--- |
| Run it in Docker or Kubernetes | [Deployment](deployment.md) |
| See which data types it finds | [Supported types](features/data-protection-pii-redaction/supported-pii-types.md) |
| Understand the design | [Architecture](architecture.md) |
| Know what it does not do | [Limitations](limitations.md) |
| Put it in front of LiteLLM, Open WebUI or LangChain | [Integrations](integrations.md) |
| Check it yourself | [Leak benchmark](conformance/index.md) |
