# chunk-invariance

A streaming filter (a redactor, a guardrail, a rewriter) is correct only if, for every way of
splitting an input into chunks, the concatenated streamed output equals the output of filtering
the whole input at once. This package turns that rule into one assertion, for Vitest, Jest or
`node:test`.

```ts
import { test } from "vitest";
import { assertChunkInvariant } from "chunk-invariance";

const redact = (text: string) => text.replaceAll("sk-AbCd1234", "[KEY]"); // whole input

function* redactStream(chunks: Iterable<string>) { // takes chunks, yields chunks
  for (const chunk of chunks) yield redact(chunk);   // redacts each chunk alone
}

test("streaming redaction", () => {
  assertChunkInvariant(redactStream, redact, "use sk-AbCd1234 now");
});
```

That test fails, and names the smallest split that breaks it:

```
ChunkInvarianceError: stream filter is not chunk-invariant
  input:    "use sk-AbCd1234 now"
  split:    ["use s","k-AbCd1234 now"] (cut at 5)
  whole:    "use [KEY] now"
  streamed: "use sk-AbCd1234 now"
  split #6 in the order tried; generated splits run fewest parts first
```

```bash
npm install --save-dev chunk-invariance
```

No runtime dependencies. ESM and CommonJS, with types. Node 18 or later.

## What it catches

- A filter that evaluates each chunk alone, so a value cut across two chunks matches neither half.
- A filter that prepends a fixed carryover from the previous chunk. The match fires, but the start
  of the value was already emitted with the previous chunk.
- A hold-back that is shorter than the pattern, a missing end-of-stream flush, and output that is
  corrupted rather than leaked (any difference from the whole-input output fails).

`chunk-invariance/examples` has one correct filter (`holdBack`) and the two wrong designs
(`designA`, `designB`) for the same pattern, as a worked comparison.

## What it does not catch

It tests a filter you can call in-process. It does not test a running gateway, where the chunks
are decided by an upstream model, a network and an SSE parser. For that, use
[`pii-leak-benchmark`](https://pypi.org/project/pii-leak-benchmark/), which sends fragmented
responses through the gateway and checks what reaches the client.

It checks the splits you ask for. The default is the whole input plus every single cut, which
catches a value cut in two. `splits: "all"` tries every split of a short input (up to 16
characters); `splits: 3` tries every split into at most three parts; an array of chunk arrays
runs exactly those, which is how to feed splits from fast-check. It cannot prove a filter correct
for inputs you did not give it.

## API

| Name | Does |
|---|---|
| `assertChunkInvariant(streamFilter, wholeFilter, text, { splits })` | Throws `ChunkInvarianceError` on the first failing split, fewest parts first. Returns the number of splits checked. |
| `assertChunkInvariantAsync(streamFilter, wholeFilter, text, { splits })` | The same for a filter that takes an `AsyncIterable<string>` and returns an async or sync iterable. `wholeFilter` may return a promise. |
| `twoPartSplits(text)` | Every split into two non-empty parts. |
| `allSplits(text, maxParts?)` | Every split into at most `maxParts` non-empty parts, the whole input first. |
| `perChunk(process, { state, flush })` | Adapts `process(chunk, state)` and `flush(state)`, with fresh state per stream. |
| `fromTransformStream(() => new TransformStream(...))` | Adapts a `TransformStream<string, string>` factory for `assertChunkInvariantAsync`. |

`streamFilter` is called once per split, so it must start from fresh state each time. Splits
fall between code points, never inside a surrogate pair.

## Background

The invariant and the two wrong designs are described in Phalak, N. (2026), *Split-Boundary
Leaks in Streaming Guardrails*, Zenodo, [10.5281/zenodo.22909585](https://doi.org/10.5281/zenodo.22909585).

Apache-2.0. A Python version with the same API is on PyPI as `chunk-invariance`.
