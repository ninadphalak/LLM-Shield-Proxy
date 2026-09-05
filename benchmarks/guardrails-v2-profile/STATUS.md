# Guardrails AI 0.10.2 v2 profile

**Read this before reading the row.** Guardrails AI **does not claim rehydration**. There is
no `reidentify`, `deanonymize` or `unredact` anywhere in its 178 modules. It is not scored
for failing to do a thing it never offered, and its FidelityRate must not be read as a
failure to restore.

The row exists for the one thing it has that nothing else inspected does.

## What it has: retention, shipping, in OSS, from someone else

`Validator.validate_stream(chunk, metadata)` does not scan a chunk in isolation:

```python
accumulated_chunks.append(chunk)
accumulated_text = "".join(accumulated_chunks)
split_contents = self._chunking_function(accumulated_text)
```

Its own docstring: *"If the LLM chunk is smaller than the validator's chunking strategy, it
will be accumulated until it reaches the desired size. In the meantime, the validator will
return None."*

That is retention across chunk boundaries. The default `_chunking_function` is
`split_sentence_word_tokenizers_jl_separator` -- it holds to the next **sentence** boundary.

## The experiment: one detector, three accumulation strategies

`gateway.py` puts the profile's own `_DETECTORS` -- the same four regexes used by
`chunk-local` and `bounded-retention` -- inside a Guardrails `Validator`, and drives them
through Guardrails' accumulator. So the detector is held constant and only the retention
strategy varies:

| policy | retention strategy | Leak (1-chunk) | Leak (adv) | DeltaFrag |
|---|---|---:|---:|---:|
| `chunk-local` | none | 0.125 | 1.00 | **0.875** |
| `bounded-retention` | `L = N-1` suffix carry | 0.125 | 0.125 | **0.00** |
| `guardrails-ai-stream-validate` | to the next sentence | 0.125 | 0.125 | **0.00** |

**Guardrails AI's sentence accumulation reproduces the reference retention policy exactly**
-- same leak rates, same DeltaFrag, and the same 6 events observed. Its residual 0.125 is
the same `EMAIL / percent / adversarial` case `bounded-retention` leaks, which is an
encoding gap and not a fragmentation one.

This is the strongest external corroboration of C2 in the repository. A third party, with no
knowledge of this profile, chose retention-before-validation and lands on the same numbers.

## And the cost of having no restore half

FidelityRate **0.00**, and here it is a real failure rather than a vacuous number: the
gateway forwards the request unmasked (there is nothing to restore it with), so the echo
segment comes back carrying the caller's real values -- and the validator redacts them,
because it has no way to tell the caller's own data from the model's.

**That is the response split's central claim, demonstrated by a product rather than by a
model.** One global policy cannot satisfy both segments. Guardrails AI redacts everything
and lands in the `redact-all` quadrant beside LiteLLM, for a different reason: LiteLLM has a
restore primitive as a dependency and does not call it; Guardrails AI has none to call.

## Configuration, and why the request path is not masked

`V2_REQUEST_PATH_REDACTION=not-configured`. Masking the request here would guarantee a
fidelity failure that says nothing about the product. The row's request-path egress is a
consequence of that choice and **is not a finding about Guardrails AI.**

## Recipe

```bash
py -3.12 -m venv venv-guardrails
venv-guardrails/Scripts/pip install guardrails-ai==0.10.2
venv-guardrails/Scripts/python benchmarks/guardrails-v2-profile/gateway.py --port 8791 &

V2_REQUEST_PATH_REDACTION=not-configured python -m pii_leak_benchmark.v2_emitter --validate \
  --only guardrails-ai-stream-validate \
  --gateway-url http://127.0.0.1:8791/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001 \
  --out benchmarks/results/v2-response-split
```

It installs `litellm`, `openai` and `langchain-core`, so it does not go in the harness
environment -- and note it would install the very gateway measured in another row.

## The hub validators are not used, and that is deliberate

Guardrails' own PII validator lives in the Hub (`hub://guardrails/detect_pii`), which needs
`guardrails configure` and an account. Using the profile's regexes instead keeps the row
free of credentials **and** holds the detector constant against `chunk-local` and
`bounded-retention`, which is the only way the accumulation strategy can be the variable.
The row therefore measures **Guardrails AI's streaming accumulator**, not Guardrails AI's
detector, and must be described that way.
