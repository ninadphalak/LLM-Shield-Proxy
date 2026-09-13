# Integration examples

These examples place LLM-Shield-Proxy on an explicit application traffic path. They are
starting points, not compatibility certifications. Pin image and package versions, run the
project's conformance suite, and exercise every API feature your application uses before a
production rollout.

| Integration | Example | Boundary to verify |
|---|---|---|
| LiteLLM | [`litellm/`](litellm/) | model aliases, authentication, streaming, tools, retries |
| Open WebUI | [`openwebui/`](openwebui/) | model discovery, chat streaming, task/RAG/audio endpoints |
| LangChain | [`langchain_chat.py`](langchain_chat.py) | `ChatOpenAI` messages, tools, structured output, streaming |
| LlamaIndex | [`llamaindex_chat.py`](llamaindex_chat.py) | OpenAI LLM adapter, callbacks, streaming, retries |
| Envoy `ext_proc` | [`envoy/envoy.yaml`](envoy/envoy.yaml) | body modes, buffer limits, timeout/failure policy, UDS permissions |
| MCP JSON-RPC gateway | [`mcp_jsonrpc.py`](mcp_jsonrpc.py) | supported methods, policy resolver, upstream URL policy, response types |

## LiteLLM: in front of the proxy, or inside it

`litellm/` ships two wirings, and they are not interchangeable.

**In front** — `litellm/docker-compose.yml`. Clients call LLM Shield, which forwards to
LiteLLM. Nothing is installed into LiteLLM. The cost is that the shield then owns the
upstream path: LiteLLM's model routing, key management, retries and budgets sit behind it,
and the shield must be trusted with the route.

**In-process** — `litellm/config.guardrail.yaml`. LiteLLM calls the shield as a guardrail and
keeps the model path. `pre_call` redacts the outbound request, `post_call` restores the reply,
and streaming replies are restored as chunks arrive rather than buffered. This needs
`pip install llm-shield-proxy` in the proxy's own environment, because LiteLLM imports
`llm_shield_proxy.integrations.litellm.guardrail.LLMShieldProxyGuardrail` from it. No file in
LiteLLM's repository is modified, so there is nothing to keep in sync with upstream.

## Common client configuration

The HTTP examples use `http://localhost:8000/v1` and a non-provider client credential.
Configure the proxy to authenticate that client and inject a separately managed upstream key.
For a local evaluation, the compose files use `OVERRIDE_CLIENT_AUTH=true`; replace that with
the deployment's identity and policy controls before production use.

Do not send real personal or regulated data during initial evaluation. Use the synthetic
fixtures from the conformance suite and inspect the serialized request received by a controlled
mock upstream.

## MCP scope

`POST /v1/mcp` currently handles selected JSON-RPC 2.0 methods: `tools/list`, `tools/call`, and
`resources/read`. It is not a complete implementation of MCP Streamable HTTP: it does not
implement MCP initialization, capability negotiation, sessions, the GET/SSE channel, or every
MCP method. The example calls this scoped gateway directly and does not claim drop-in support
for arbitrary MCP SDKs.
