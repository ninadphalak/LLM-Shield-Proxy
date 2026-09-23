# Split-Boundary Leaks in Streaming Guardrails

*A defect class found independently in five LLM streaming stacks that share no code.*

Published 2026-09-22.

## Summary

Five LLM streaming stacks that share no code have each shipped the same defect: a guardrail that inspects a streamed response chunk by chunk, so a sensitive value split across a chunk boundary reaches the client unmasked.

The instances were found between February and September 2026, in TypeScript and Python, in proxies, agent frameworks and SDK middleware. Two are fixed, two are open, and one vendor resolved it by changing their documentation rather than their code. None of the five cites any of the others.

That pattern is the point. Five teams independently reached for the same two designs, evaluating each chunk alone, or prepending a fixed number of trailing characters from the previous one, and both are wrong for the same reason. This is not five mistakes. It is a missing shared invariant, and the absence of a name for it is why each team had to rediscover it.

The invariant is stated in the next section. It fits in one sentence and it is testable in one assertion.

## The missing invariant

A streaming filter is correct when its output does not depend on how the input was chunked. For any way of splitting the same input, the concatenated streamed output must be byte-identical to filtering the whole string at once. Nothing weaker is sufficient, and every one of the five failures below violates it.

The reason weaker properties fail is that **detection is not prevention**. A filter can correctly identify a split value and still have leaked it, because the first fragment was already written to the wire in the previous chunk. Masking it afterwards is a correction the stream has no way to deliver: the client has the bytes, the browser has rendered them, the log has recorded them.

This is what makes the class hard to see in review. The code contains a redaction call, the redaction call fires, and the processor logs a successful redaction, while the raw value is already gone. Mastra's implementation did exactly this, and the logging is what made it look correct.

So the operative rule for an implementer is narrower than "buffer enough to match":

> Text may not be emitted until it is known that no match could still grow into it.

## The five instances

| Project | Language | Mechanism | Severity as filed | Status |
| --- | --- | --- | --- | --- |
| [LiteLLM](https://github.com/BerriAI/litellm/issues/41611) | Python | Per-chunk path leaks; buffered path silently stops streaming | Bug | Open, fix PR unmerged |
| [Mastra](https://github.com/mastra-ai/mastra/issues/23783) | TypeScript | 128-char carryover detected the match, emitted it anyway | Critical | Fixed in 4 days |
| [LangChain](https://github.com/langchain-ai/langchain/issues/35011) | Python | Guardrails ran after the model, not in the stream path | Bug | Fixed Jun 2026 |
| [NVIDIA NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails/issues/2375) | Python | Output masking rail unusable in 0.24.0 | Bug | Open |
| [Vercel AI SDK](https://ai-sdk.dev/docs/ai-sdk-core/middleware) | TypeScript | `wrapStream` guardrail example left unimplemented | Documented, not fixed | Docs warn |

**LiteLLM** has neither a boundary bug nor a fix. It has both branches, selected by a flag. Guardrails on the translated path are evaluated per chunk with no carryover. A guardrail with `apply_to_output=True` takes the native path into `_stream_apply_output_masking`, which accumulates every chunk and returns a single chunk containing the whole response. The safe branch is correct and does not stream; the streaming branch is not safe; most operators will not know the flag decides that. The same function has three paths that emit buffered chunks **unmasked**: a bare `except Exception` fallback, a mixed-chunk-type branch whose own comment says so, and an unknown-event passthrough.

**Mastra** is the clearest illustration of detection without prevention. The processor prepended 128 characters of the previous chunk so a split address would match, and the docstring said so. The match fired, the processor logged a successful redaction, and the raw value went out, because the first fragment had already been emitted. Their triage bot confirmed it as critical, reproducing both a complete raw SSN in the clear and deletion of ordinary text on overlapping detections. Fixed within four days.

**LangChain** is the same class approached from a different angle: guardrails ran on the completed model output rather than in the stream path, so streaming bypassed them entirely. The maintainer scoped the fix to PII middleware specifically and left general output-guarding middleware open as a separate problem, which means the class is only partly closed there.

**NeMo Guardrails** fails earlier. The masking call raises on an unexpected keyword argument in 0.24.0, so the output rail cannot run at all. Different failure, same consequence: no masking on the output path.

**Vercel** did not ship a bug; they wrote the warning. Their middleware docs now state that an incremental implementation must retain every possible incomplete match, and that a fixed-size buffer alone is not safe for unbounded variable-length patterns. The `wrapStream` guardrail example is deliberately left unimplemented. That is the most honest position of the five, and it is still not a fix any user can install.

## Why it keeps being built wrong

Two designs look right and are not. Every instance above is one or the other.

**Design A, evaluate each chunk alone.** The filter runs on each delta as it arrives. A value split across a boundary matches neither half, so nothing fires and the client reassembles the original. This is the naive default, and it is what LiteLLM's translated path and LangChain's post-model hook both did.

**Design B, prepend a fixed carryover.** Hold the last N characters of the previous chunk, prepend them to the current one, run the filter on the join. This is the fix everyone reaches for second, and it is worse than design A because it *appears* to work: the match now fires, the redaction logs, the test on the reassembled output passes.

It still leaks, for two independent reasons.

1. **The first fragment is already gone.** Redacting on the join rewrites only what has not shipped. The leading characters left with the previous chunk; masking the remainder yields a leak and a corrupted response at the same time.
2. **The window is smaller than the patterns.** N must exceed the longest possible match, and several patterns have no longest match. A JWT runs to hundreds of characters, some provider keys have no fixed length, a PEM block is unbounded by definition. A 128-character window against an unbounded pattern is a coin flip.

The second reason is why Vercel's docs single out variable-length patterns, and why "just make the buffer bigger" is not a fix. It moves the threshold without removing it.

There is a third failure mode worth naming because it does not look like a leak: **rewriting the buffer in place**. If masked text is written back into the same buffer the filter scans, then indices, dedupe keys and re-scan positions all drift against the original offsets. Mastra's second reported symptom, ordinary text deleted on overlapping detections, came from this, not from the boundary.

## What a correct fix guarantees

Both workable designs follow from the same rule: text is not emitted until no match could still grow into it. They differ only in how much they hold.

**Design 1, buffer to a natural boundary.** Accumulate to the end of a sentence, a block, or the whole response, filter, then emit. Simple, provably correct, and what Vercel's docs recommend as the safe default. The cost is that it delays output and holds memory proportional to the block. Taken to its limit it is not streaming at all, which is exactly what LiteLLM's `apply_to_output` path already does, without telling the operator.

**Design 2, settlement point.** Per chunk, compute the furthest position that no pattern could still extend past. Emit up to there; hold the tail. On ordinary prose the held tail is a few characters, so the stream keeps flowing. This is harder to implement and it is the only design that preserves streaming while satisfying the invariant.

Design 2 has one decision that must be made explicitly, and it is where the class reappears:

> When the held tail reaches its ceiling and a variable-length pattern is still open, the implementation either releases text that may be the first half of a secret, or it refuses.

These are not equally safe. A bounded tail that releases on overflow is design B wearing a better name: it has a threshold, and an adversary who knows the threshold can cross it. **The correct behaviour on overflow is to fail closed**, blocking, erroring or truncating the response, but not emitting. A ceiling is still necessary, or a pathological input with no delimiters pins the buffer. The ceiling bounds memory; it does not license release.

Two implementation notes that cost real bugs:

- **Keep the raw buffer pristine.** Mask on the way out, never in place. Rewriting the scanned buffer is what drifts indices and deletes text on overlapping matches.
- **Detect on a canonical view.** If matching runs on the raw bytes, Unicode look-alikes walk a value past an ASCII pattern. Canonicalise for detection, mask against raw offsets.

## The test that catches it

One assertion covers the whole class:

```python
def test_chunking_invariance(filter_stream, filter_whole, corpus):
    for text in corpus:
        expected = filter_whole(text)
        for size in range(1, len(text) + 1):
            chunks = [text[i:i + size] for i in range(0, len(text), size)]
            assert "".join(filter_stream(chunks)) == expected
```

Parametrised down to a chunk size of one character, this fails on every design A and design B implementation, and passes on both correct designs. It catches boundary leaks, double-masking and partial redaction together, and it fails loudly rather than silently.

**Why the obvious assertions do not work.** Three weaker tests are commonly written, and a design B implementation passes all three:

| Assertion | Why it passes buggy code |
| --- | --- |
| Reassembled output contains no raw PII | Holds only if the mask covers both fragments; a first-fragment leak plus a masked second still concatenates to something without the full value |
| The redaction callback fired | Detection is not prevention. It fires on the join, after the leak |
| A fixed split point is masked | Tests the one boundary the author thought of, not the one the tokenizer produces |

A second assertion is worth adding alongside, because it localises the failure rather than just detecting it:

> No emitted chunk, and no prefix of the concatenation, may contain a fragment of a value that is later masked.

The first assertion tells you the implementation is wrong. The second tells you which chunk did it.

**What to put in the corpus.** Fixed-length patterns exercise the boundary; variable-length ones exercise the ceiling. Include at least one unbounded pattern, a PEM block or a long JWT, or the suite will pass on an implementation that releases on overflow. Include a value immediately adjacent to non-ASCII text, and a script written without spaces, since a tail bounded by word delimiters grows without limit there.

## Open questions

- **The latency cost of design 2 is unmeasured across implementations.** The claim that a held tail is a few characters on ordinary prose is plausible and is what the settlement-point implementations report, but no one has published a comparison of time-to-first-token against an unfiltered stream. That number decides whether maintainers accept design 2 or settle for design 1.
- **No project has adopted the invariant as a stated contract.** Vercel documents the hazard, Mastra fixed an instance, LangChain fixed a subset. None publishes chunking-invariance as a property their guardrail guarantees, which is what would let a user tell a correct implementation from a plausible one.
- **LangChain's general case is still open.** The June fix scoped to PII middleware; output-guarding middleware in general was left as a separate problem.
- **LiteLLM's fail-open paths are unaddressed.** The three branches that emit buffered chunks unmasked on error, mixed chunk types, or unknown events are independent of the boundary bug and are arguably more severe. A guardrail whose failure mode is emitting the PII fails silently under exactly the conditions, an overloaded or unreachable analyzer, where it matters most.
- **Is there a sixth instance?** The five here were found by looking. The pattern suggests the right question is not whether other streaming guardrails have it, but which ones do not.

## Citing this page

Cite as: Ninad Phalak, "Split-Boundary Leaks in Streaming Guardrails", 2026-09-22, https://llmshieldproxy.com/docs/split-boundary-leaks

## Sources

- [BerriAI/litellm#41611](https://github.com/BerriAI/litellm/issues/41611), streaming guardrails, split SSE chunks
- [BerriAI/litellm#41936](https://github.com/BerriAI/litellm/pull/41936), proposed fix, unmerged
- [mastra-ai/mastra#23783](https://github.com/mastra-ai/mastra/issues/23783), PIIDetector emits PII in the clear
- [langchain-ai/langchain#35011](https://github.com/langchain-ai/langchain/issues/35011), streaming bypasses guardrails
- [NVIDIA-NeMo/Guardrails#2375](https://github.com/NVIDIA-NeMo/Guardrails/issues/2375), output masking rail unusable in 0.24.0
- [Vercel AI SDK middleware docs](https://ai-sdk.dev/docs/ai-sdk-core/middleware), incremental redaction guidance
- LiteLLM source read at `litellm/proxy/guardrails/guardrail_hooks/presidio.py` and `litellm/proxy/utils.py`, `main` as of 2026-09-22
