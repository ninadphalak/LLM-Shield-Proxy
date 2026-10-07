# LLM Guard 0.3.16 v2 profile

**Why this row matters more than the other library rows.** LLM Guard is the only product
this survey found that ships **every** piece the v2 response split asks for, from one
vendor:

| piece | LLM Guard component |
|---|---|
| mask the request, remember the mapping | `input_scanners.Anonymize` + `Vault` |
| redact PII in the model's reply | `output_scanners.Sensitive` |
| restore the caller's own values | `output_scanners.Deanonymize` |

The profile's whole premise is that a correct gateway must do two **opposite** things to one
response. LLM Guard is the one place where both halves are already implemented, tested and
shipped by the same people. If it holds under fragmentation, the paper's problem is an
integration problem. If it does not, the problem is where the paper says it is.

**It is not a gateway.** `gateway.py` here is the thinnest possible wrapper: it detects
nothing, masks nothing, and makes exactly one decision.

## The one decision, which is the experiment

`Sensitive.scan(prompt, output)` and `Deanonymize.scan(prompt, output)` both take the
**complete** model output. A streaming integrator must therefore choose:

- **`chunk-local`** -- scan each SSE delta as it arrives. Incremental delivery survives; a
  value split across two deltas is never whole in front of the scanner.
- **`buffered`** -- accumulate the whole response, scan once, emit one chunk. The scanner
  always sees whole values; incremental delivery is gone.

Both are faithful uses of the published API, and the API offers no third option. The two
rows measure what each choice costs.

## Cost, stated plainly, because "it is free" is not the same as "it is cheap"

`llm-guard==0.3.16` declares `requires_python: <3.13,>=3.10` and pins `torch>=2.4.0`,
`transformers==4.51.3`, `presidio-analyzer==2.2.358` and `presidio-anonymizer==2.2.358`.

- It **cannot** be installed alongside this harness, which runs CPython 3.14.
- It is a multi-gigabyte install. No credentials and no account, but not a light row.

## Recipe

```bash
py -3.12 -m venv venv-llmguard
venv-llmguard/Scripts/pip install llm-guard==0.3.16

# one row per integration choice, against the harness capture on 8799
LLMGUARD_MODE=chunk-local venv-llmguard/Scripts/python \
  benchmarks/llm-guard-v2-profile/gateway.py --port 8790 &

python -m pii_leak_benchmark.v2_emitter --validate --only llm-guard-chunk-local \
  --gateway-url http://127.0.0.1:8790/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001 \
  --out benchmarks/results/v2-response-split
```

Repeat with `LLMGUARD_MODE=buffered` and `--only llm-guard-buffered`.

`V2_REQUEST_PATH_REDACTION=configured` for both: `Anonymize` runs over the whole request
body, so this target **is** asked to mask the request path and a request-path egress would
be a finding about it rather than about our configuration.

## `Sensitive(redact=False)` is the default, and it detects without redacting

`output_scanners.Sensitive.__init__` takes `redact: bool = False`. On the default it runs
the Presidio analyzer, logs `Found sensitive data in the output`, returns
`is_valid=False` -- and returns the output **unchanged**. Verified before any row was run:

```
[warning] Found sensitive data in the output results=[type: EMAIL_ADDRESS, start: 94, ...]
redacted: ... Reference record: afznaotg@example.com      <- unchanged
```

`gateway.py` passes `redact=True`. A row on the default would have scored LeakRate 1.00 and
been a fact about this wrapper rather than about LLM Guard, which is the error this results
directory already records twice.

**It is worth one sentence in the paper as an observation, not a defect.** The safe reading
is that `Sensitive` is a detector whose redaction is opt-in, in the same family as Portkey's
guardrail failing open and NeMo's mask rail being unusable: the default posture of a
response-side control is not to modify the response.

## The wrapper builds the scanners ONCE, and the first version did not

`gateway.py` constructs `Anonymize`, `Sensitive` and `Deanonymize` at module scope and
clears the shared `Vault` per request under a lock. The first version built all three per
request. Each one builds a Presidio analyzer and loads its recognizers, so a 6-seed sweep
died partway through the fourth seed and the harness reported **all 32 cases inconclusive**
-- which is the "target answered nothing" guard working: it refused to record an all-zeros
row as a clean one.

**That was this wrapper's bug and it is not a property of LLM Guard.** Recorded because the
fix changes behaviour (shared vault, serialized requests) and the rows were therefore
re-verified rather than assumed: both reproduce their published numbers exactly,
`1.00 / 0.25 / 0.75 / 0.50` and `1.00 / 0.3125 / 0.3125 / 0.00`.

## Order of the output scanners is load-bearing

`Sensitive` first, `Deanonymize` second. Reversed, `Deanonymize` restores the caller's real
values and `Sensitive` then removes them again -- destroying the fidelity half in order to
satisfy the leak half, and scoring the product for a mistake the wrapper made.
