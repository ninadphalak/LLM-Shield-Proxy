# Running behind LiteLLM

LiteLLM ships LLM Shield Proxy as a built-in guardrail, `guardrail: llm_shield_proxy`
([BerriAI/litellm#42645](https://github.com/BerriAI/litellm/pull/42645), merged
2026-10-05). Its setup is documented by LiteLLM:
[LLM Shield Proxy guardrail](https://docs.litellm.ai/docs/proxy/guardrails/llm_shield_proxy).

```yaml
guardrails:
  - guardrail_name: llm-shield
    litellm_params:
      guardrail: llm_shield_proxy
      mode: [pre_call, post_call]
      default_on: true
      api_base: http://localhost:8000
      api_key: os.environ/LLM_SHIELD_PROXY_API_KEY
```

It is on LiteLLM's `main` branch and not yet in a tagged LiteLLM release; v1.105.0-rc.1
and earlier do not include it. On a LiteLLM without the module, that config does not stop the proxy: LiteLLM logs one
error line, `Skipping guardrail 'llm-shield': invalid configuration, proxy is starting WITHOUT
this guardrail: Unsupported guardrail: llm_shield_proxy`, starts, and every request reaches the
provider unredacted (seen on 1.105.0, the newest image). Check that line is absent from the
startup log before sending anything real.
On those versions, use one of the two wirings below. Neither changes a file in LiteLLM's
repository.

| | Built-in guardrail | In-process guardrail | Generic guardrail API |
|---|---|---|---|
| Wiring | `guardrail: llm_shield_proxy` | LiteLLM loads a class from this repository's example by dotted path | LiteLLM calls an HTTP endpoint that follows its contract |
| LiteLLM version | a build of LiteLLM `main` that contains commit `d9467067`; no tagged release yet | any with custom guardrails | any with `generic_guardrail_api` |
| What runs beside the proxy | nothing | nothing (you mount one file) | a small shim process |
| Guardrails dashboard card | yes | no | no |
| Listed in LiteLLM's docs | yes | no | no |
| Streaming restoration | yes | yes | yes, opt-in on both sides |
| Tool-call arguments restored | yes | yes | no: LiteLLM's response contract has no field for them |

## In-process guardrail

LiteLLM imports the class itself, so the file must be importable by the proxy process. Copy or
mount `examples/integrations/litellm/litellm_guardrail.py` where the proxy can reach it. LiteLLM
documents this pattern for custom guardrails:

```shell
-v $(pwd)/litellm_guardrail.py:/app/litellm_guardrail.py
```

It is an example artifact rather than a module of the published package, because it imports
LiteLLM at module scope and `tests/ootb/_import_every_module.py` requires every packaged module to
import on a wheel-only install.

```yaml
guardrails:
  - guardrail_name: "llm-shield"
    litellm_params:
      guardrail: litellm_guardrail.LLMShieldProxyGuardrail
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

One thing this path cannot do: LiteLLM's generic-guardrail contract carries `tool_calls`
**in** but has no field to carry them back, so a tool call's arguments come back holding
placeholders and nothing raises. There is no shim-side fix: the value has nowhere to go.
`GENERIC_GUARDRAIL_CONTRACT.md` records the source-level reason. If your workload calls
tools whose arguments carry redactable text, use the in-process guardrail, which restores
them.

## Streaming

The built-in guardrail restores streams through its own streaming hook and needs none of the
settings below. On the in-process and generic wirings, streaming restoration is opt-in, because
the defaults break it silently:

- LiteLLM's `streaming_transform_mode` defaults to `block_only`, which **discards a
  rewriting guardrail's output on the streaming path**. Set `incremental_diff`.
- `streaming_sampling_rate` defaults to `5`, i.e. one chunk in five. Set `1`.
- On the generic path the shim also needs `SHIM_STREAMING_REHYDRATION=true`. LiteLLM's
  request carries no "am I streaming" field, so the shim cannot infer it, and applying
  a holdback to a non-streaming response would withhold a tail that never gets flushed.

The shim makes two calls to the Shield per round while streaming, because LiteLLM
forces the holdback to 0 on the final round and emits the withheld region verbatim. If
that region were the raw placeholder, every reply ending on a redacted value would
finish by showing the user a placeholder, which is the failure this product exists to
prevent. `tests/integrations/litellm/test_streaming_holdback.py` proves both the
mapping and that the naive single-call version really does reproduce that defect.

## Known limits

- **A restored value inside a tool call's `arguments` is spliced into a JSON string.** The
  arguments are a JSON document, so a restored value containing a double quote, a backslash
  or a newline can leave that document unparseable by a strict parser. The Shield's own JSON
  rehydration has the same property. Escaping is deliberately not applied as a fix: a
  streamed fragment is an arbitrary slice of a JSON document, so the code cannot tell whether
  the position it writes is inside a string literal, and escaping unconditionally would
  corrupt the values that are not.
- **The generic guardrail API path cannot restore tool arguments at all**, see above.
- **LiteLLM's `incremental_diff` mode covers string `delta.content` only**, so streamed
  tool-argument restoration through that path is out of scope upstream. The in-process
  guardrail restores them per tool call.

## Further reading

- [LLM Shield Proxy guardrail](https://docs.litellm.ai/docs/proxy/guardrails/llm_shield_proxy):
  LiteLLM's page for the built-in guardrail, including the LiteLLM SDK path
- `examples/integrations/litellm/GENERIC_GUARDRAIL_CONTRACT.md`: the LiteLLM contract,
  read out of LiteLLM's source
- `examples/integrations/README.md`: the other integration examples
