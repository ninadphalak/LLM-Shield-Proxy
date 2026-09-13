# Running behind LiteLLM

LLM Shield can sit on a LiteLLM request path two ways. They trade discoverability
against effort, and you can use either without changing a single file in LiteLLM's
repository.

| | In-process guardrail | Generic guardrail API |
|---|---|---|
| Wiring | LiteLLM loads a class from this package by dotted path | LiteLLM calls an HTTP endpoint that follows its contract |
| LiteLLM code touched | none | none |
| What runs beside the proxy | nothing (LiteLLM imports `llm_shield_proxy`) | a small shim process |
| Guardrails dashboard card | no | no |
| Listed in LiteLLM's docs | no | no |
| Streaming restoration | yes | yes, opt-in on both sides |
| Conflicts with LiteLLM releases | none | none |

Neither gets you a card in LiteLLM's Admin UI or a page in LiteLLM's own docs: both
of those exist only by editing LiteLLM's files. If being listed matters, that is a
conversation with LiteLLM, not a configuration choice.

## In-process guardrail

LiteLLM, `litellm` and `llm_shield_proxy` must be importable in the same environment,
because LiteLLM imports the class itself.

```yaml
guardrails:
  - guardrail_name: "llm-shield"
    litellm_params:
      guardrail: llm_shield_proxy.integrations.litellm.guardrail.LLMShieldProxyGuardrail
      mode: ["pre_call", "post_call"]
      default_on: true
      api_base: http://localhost:8000
      api_key: os.environ/LLM_SHIELD_PROXY_API_KEY
```

`mode` takes both halves on one entry. `pre_call` redacts the outbound request and
`post_call` restores the reply; registering `pre_call` alone redacts the request and
hands the placeholders straight back to the caller.

See `examples/integrations/litellm/config.guardrail.yaml`.

## Generic guardrail API

LiteLLM's built-in `generic_guardrail_api` posts to
`/beta/litellm_basic_guardrail_api` on a base URL you give it. The shim in
`examples/integrations/litellm/litellm_guardrail_shim.py` answers that contract and
forwards to the Shield's existing `/v1/guard/redact` and `/v1/guard/rehydrate`
endpoints, so nothing new touches the vault.

```shell
export SHIELD_BASE_URL=http://127.0.0.1:8000
export SHIELD_API_KEY=<a virtual key configured on the Shield>
export LITELLM_GUARDRAIL_SHIM_KEY=<the key LiteLLM sends as x-api-key>
uvicorn litellm_guardrail_shim:app --host 127.0.0.1 --port 8100
```

```yaml
guardrails:
  - guardrail_name: "llm-shield"
    litellm_params:
      guardrail: generic_guardrail_api
      mode: ["pre_call", "post_call"]
      api_base: http://127.0.0.1:8100
      api_key: os.environ/LLM_SHIELD_PROXY_API_KEY
      unreachable_fallback: fail_closed
```

Give the shim its **own** Shield virtual key. The Shield scopes a vault by
`(session_id, virtual_key_id)`, so every caller through one shim shares a key and
session isolation rests on LiteLLM's per-call id. Keep the shim on loopback or a
private interface, and treat its key as equivalent in power to the Shield key it
uses. See `examples/integrations/litellm/config.generic_guardrail.yaml`.

## Streaming

Streaming restoration works on both wirings, and on both it is opt-in — the defaults
break it silently:

- LiteLLM's `streaming_transform_mode` defaults to `block_only`, which **discards a
  rewriting guardrail's output on the streaming path**. Set `incremental_diff`.
- `streaming_sampling_rate` defaults to `5`, i.e. one chunk in five. Set `1`.
- On the generic path the shim also needs `SHIM_STREAMING_REHYDRATION=true`. LiteLLM's
  request carries no "am I streaming" field, so the shim cannot infer it, and applying
  a holdback to a non-streaming response would withhold a tail that never gets flushed.

The shim makes two calls to the Shield per round while streaming, because LiteLLM
forces the holdback to 0 on the final round and emits the withheld region verbatim. If
that region were the raw placeholder, every reply ending on a redacted value would
finish by showing the user a placeholder — which is the failure this product exists to
prevent. `tests/integrations/litellm/test_streaming_holdback.py` proves both the
mapping and that the naive single-call version really does reproduce that defect.

## Further reading

- `examples/integrations/litellm/GENERIC_GUARDRAIL_CONTRACT.md` — the LiteLLM contract,
  read out of LiteLLM's source
- `examples/integrations/README.md` — the other integration examples
