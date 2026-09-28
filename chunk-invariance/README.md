# chunk-invariance

A streaming filter (a redactor, a guardrail, a rewriter) is correct only if, for every way of
splitting an input into chunks, the concatenated streamed output equals the output of filtering
the whole input at once. This package turns that rule into one assertion.

```python
from chunk_invariance import assert_chunk_invariant

def redact(text):                 # your filter on the whole input
    return text.replace("sk-AbCd1234", "[KEY]")

def redact_stream(chunks):        # your filter on a stream: takes chunks, yields chunks
    for chunk in chunks:
        yield redact(chunk)       # redacts each chunk alone

def test_streaming_redaction():
    assert_chunk_invariant(redact_stream, redact, "use sk-AbCd1234 now")
```

That test fails, and names the smallest split that breaks it:

```
chunk_invariance.core.ChunkInvarianceError: stream filter is not chunk-invariant
  input:    'use sk-AbCd1234 now'
  split:    ['use s', 'k-AbCd1234 now'] (cut at 5)
  whole:    'use [KEY] now'
  streamed: 'use sk-AbCd1234 now'
  split #6 in the order tried; generated splits run fewest parts first
```

```bash
pip install chunk-invariance
pip install "chunk-invariance[hypothesis]"   # optional Hypothesis strategies
```

## What it catches

- A filter that evaluates each chunk alone, so a value cut across two chunks matches neither half.
- A filter that prepends a fixed carryover from the previous chunk. The match fires, but the start
  of the value was already emitted with the previous chunk.
- A hold-back that is shorter than the pattern, a missing end-of-stream flush, and output that is
  corrupted rather than leaked (any difference from the whole-input output fails).

`chunk_invariance.examples` has one correct filter (`hold_back`) and the two wrong designs
(`design_a`, `design_b`) for the same pattern, as a worked comparison.

## What it does not catch

It tests a filter you can call in-process. It does not test a running gateway, where the chunks
are decided by an upstream model, a network and an SSE parser. For that, use
[`pii-leak-benchmark`](https://pypi.org/project/pii-leak-benchmark/), which sends
fragmented responses through the gateway and checks what reaches the client.

It checks the splits you ask for. The default is the whole input plus every single cut, which
catches a value cut in two. `splits="all"` tries every split of a short input (up to 16
characters); `splits=3` tries every split into at most three parts; an explicit list of chunk
lists runs exactly those. It cannot prove a filter correct for inputs you did not give it.

## API

| Name | Does |
|---|---|
| `assert_chunk_invariant(stream_filter, whole_filter, text, *, splits="all-two-part")` | Raises `ChunkInvarianceError` (an `AssertionError`) on the first failing split, fewest parts first. Returns the number of splits checked. Works on `str` and `bytes`. |
| `assert_chunk_invariant_async(...)` | The same, for an async generator filter, inside an async test. |
| `two_part_splits(text)` | Every split into two non-empty parts. |
| `all_splits(text, max_parts=None)` | Every split into at most `max_parts` non-empty parts, the whole input first. |
| `per_chunk(process, *, state=dict, flush=None)` | Adapts `process(chunk, state) -> output` and `flush(state) -> output`, with fresh state per stream. |
| `from_async(async_stream_filter)` | Adapts an async generator filter for the synchronous assertion. |
| `strategies.splits_of(text, max_parts=None)` | Hypothesis strategy: random splits of `text`, shrinking toward fewer cuts. |
| `strategies.text_and_splits(texts, max_parts=None)` | Hypothesis strategy: `(text, chunks)` pairs. |

`stream_filter` is called once per split, so it must start from fresh state each time.

## Background

The invariant and the two wrong designs are described in Phalak, N. (2026), *Split-Boundary
Leaks in Streaming Guardrails*, Zenodo, [10.5281/zenodo.22909585](https://doi.org/10.5281/zenodo.22909585).

Standard library only. Apache-2.0. A TypeScript version with the same API is published on npm
as `chunk-invariance`.
