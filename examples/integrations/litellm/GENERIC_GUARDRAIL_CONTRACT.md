# LiteLLM "Generic Guardrail API" — the contract, extracted

Read out of LiteLLM's source at `litellm_internal_staging`, not inferred from the docs prose.
Source files (paths are inside `BerriAI/litellm`):

| What | File |
|---|---|
| Client (what LiteLLM does to you) | `litellm/proxy/guardrails/guardrail_hooks/generic_guardrail_api/generic_guardrail_api.py` |
| Config surface | `.../generic_guardrail_api/__init__.py`, `.../example_config.yaml` |
| Server side (their reference impl) | `cookbook/mock_guardrail_server/mock_bedrock_guardrail_server.py` |
| Streaming driver | `litellm/proxy/guardrails/guardrail_hooks/unified_guardrail/unified_guardrail.py` |

The point of this contract: integration needs **no PR to LiteLLM**. Their own docs page
`adding_provider/generic_guardrail_api` lists the three reasons to make one, and the only
relevant one is *"You want to be featured as a built-in provider."*

## Endpoint and auth

- You expose `POST /beta/litellm_basic_guardrail_api` at your base URL.
- `api_base` in the config is the **base**; LiteLLM appends the path itself
  (`if not base_url.endswith("/beta/litellm_basic_guardrail_api")`). Passing the full path
  also works.
- Auth is the header **`x-api-key`**, set from `litellm_params.api_key`. Inbound client headers
  are NOT forwarded verbatim — they pass through an allowlist (`host`, `accept-encoding`,
  `connection`, `accept`, `content-type`, `user-agent`, `content-length`, and the globs
  `x-stainless-*`, `x-litellm-*`); anything else is replaced with the literal `[present]`.

## Request body

```jsonc
{
  "texts": ["..."],                    // required, the extracted text
  "input_type": "request",             // "request" | "response"  <- drives everything
  "images": null,                      // base64 or URLs
  "tools": null,                       // tool definitions (OpenAI spec)
  "tool_calls": null,                  // tool calls (OpenAI spec)
  "structured_messages": null,         // full messages, OpenAI format
  "request_data": {},                  // user_api_key_hash/_alias/_user_id/_user_email/_team_id/_team_alias
  "additional_provider_specific_params": {},
  "litellm_call_id": "...",            // per-call id  <- the rehydration correlation key
  "litellm_trace_id": "...",
  "request_headers": {},               // sanitized, see above
  "litellm_version": "...",
  "model": null
}
```

## Response body

```jsonc
{
  "action": "NONE",                    // "BLOCKED" | "NONE" | "GUARDRAIL_INTERVENED"
  "blocked_reason": null,              // only meaningful for BLOCKED
  "texts": null,                       // replacement texts, when intervening
  "images": null,
  "stream_holdback_chars": null        // see Streaming below
}
```

Behaviour, from `apply_guardrail` / `_build_guardrail_return_inputs`:

- `BLOCKED` raises `GuardrailRaisedException` using `blocked_reason` (default `"Content violates policy"`).
- Otherwise, if `texts` is present it **replaces the texts wholesale** — it is not a patch, and the
  list must be the same length and order as what you were sent.
- `stream_holdback_chars` is forwarded into the streaming driver.

## Config knobs

```yaml
guardrail: generic_guardrail_api
mode: [pre_call, post_call]     # pre_call | post_call | during_call, or a list
api_base: https://your-shim     # required unless GENERIC_GUARDRAIL_API_BASE is set
api_key: os.environ/...         # becomes the x-api-key header
unreachable_fallback: fail_closed   # default. fail_open only for 502/503/504 or when fail_on_error is false
fail_on_error: true                 # default
default_on: false
headers: {}                     # extra outbound headers
extra_headers: []               # inbound header names to additionally forward
additional_provider_specific_params: {}
streaming_transform_mode: ...   # see below
streaming_sampling_rate: ...
streaming_end_of_stream_only: ...
```

## Streaming — the part that matters

Three knobs, all read by `UnifiedLLMGuardrails.async_post_call_streaming_iterator_hook`.
The defaults are hostile to a rewriting guardrail:

| Knob | Default | Meaning |
|---|---|---|
| `streaming_transform_mode` | `block_only` | `"block_only"` (default) **drops text rewrites on the streaming path**; `"incremental_diff"` emits them as synthetic deltas |
| `streaming_sampling_rate` | `5` | `sampling_rate=1` means every chunk, `5` means every 5th chunk. Must be `>= 1` |
| `streaming_end_of_stream_only` | `false` | apply only at end of stream, not per chunk |

For reversible restoration you need `streaming_transform_mode: incremental_diff`. Two further
caveats, both from the source:

- `incremental_diff` is **only supported on the OpenAI chat completions streaming path with a
  resolvable request route**; otherwise LiteLLM logs a warning and falls back to `block_only`.
- The framework **fails closed** with `HTTP 400 stream_transform_underflow` if your returned text
  is not a forward extension of what has already been streamed. It states the mitigation itself:
  *"Withhold recent output via stream_holdback_chars before rewriting it."* Emitted bytes cannot
  be retracted.

The streaming model is: LiteLLM accumulates the response per choice, hands you the **accumulated**
text via `texts`, and takes back your **mutated accumulated** text plus a per-choice holdback. It
emits `text[len(already_emitted) : len(text) - holdback]`. On the final flush the holdback is forced
to 0. That maps directly onto a sliding-window rehydration buffer: the withheld tail is exactly the
carry the buffer refuses to emit yet.

## Why this matters for us

Two things line up exactly with the in-tree guardrail:

- `litellm_call_id` is sent on **every** call, so the redact call and the matching rehydrate call can
  share a session vault. That is the correlation the in-tree version gets from holding state on the
  request dict.
- `stream_holdback_chars` exists to let a rewriting guardrail hold back a trailing window before
  rewriting — the same job `SSERehydrationBuffer.content_buffer` does.

## How the holdback composes with the rehydration buffer (validated)

This was the one untested assumption, and it is now proved against the real
`SSERehydrationBuffer` in `tests/integrations/litellm/test_streaming_holdback.py`.

The framework wants the **accumulated** mutated text plus a holdback, and emits
`text[len(already_emitted) : len(text) - holdback]`. The buffer's `process_delta_text` keeps its
withheld tail in `content_buffer` and returns everything else, and it can re-derive that tail from
the accumulated text alone — so the shim needs no per-stream state. Concretely:

- the **withheld length** comes from `/v1/guard/rehydrate/stream`, called with the whole accumulated
  text and an empty carry;
- the **restored text** comes from `/v1/guard/rehydrate`;
- the shim returns `texts = [restored]` with `stream_holdback_chars = len(carry)`.

The second call is not redundant. LiteLLM forces the holdback to 0 on the final round, so whatever
the shim withholds there is emitted verbatim — and if that region held the raw placeholder, every
reply ending on a redacted value would finish by showing the user a placeholder. `emitted + carry`
is the obvious single-call mapping and it reproduces exactly that defect; a negative-control test
asserts so, which is what makes the two-call version evidence rather than preference.

The cost is one extra call to the Shield per streaming round. Over a loopback interface that is
cheaper than the failure it avoids.

