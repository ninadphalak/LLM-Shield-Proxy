---
slug: /chunk-invariance
title: chunk-invariance
sidebar_label: Overview
sidebar_position: 1
description: chunk-invariance is a one-line test assertion for streaming filters such as redactors and guardrails. It fails when a value split across two chunks slips through, and names the smallest split that breaks the filter. Python and TypeScript.
keywords: [streaming guardrail testing, split-boundary leak, chunk invariance, streaming redaction test, property-based testing, Python, TypeScript]
---

# chunk-invariance

A streaming filter, such as a redactor, guardrail or rewriter, is correct only if every way
of splitting the input into chunks streams to the same output as filtering the whole input at
once. `chunk-invariance` turns that rule into one test assertion.

```python
from chunk_invariance import assert_chunk_invariant

def redact(text):                 # your filter on the whole input
    return text.replace("sk-AbCd1234", "[KEY]")

def redact_stream(chunks):        # your filter on a stream
    for chunk in chunks:
        yield redact(chunk)       # redacts each chunk alone

def test_streaming_redaction():
    assert_chunk_invariant(redact_stream, redact, "use sk-AbCd1234 now")
```

That test fails and names the smallest split that breaks the filter:

```
stream filter is not chunk-invariant
  split:    ['use s', 'k-AbCd1234 now'] (cut at 5)
  whole:    'use [KEY] now'
  streamed: 'use sk-AbCd1234 now'
```

```bash
pip install chunk-invariance     # Python, standard library only
npm install chunk-invariance     # TypeScript: Vitest, Jest or node:test
```

## What it catches

- A filter that checks each chunk alone, so a value cut in two matches neither half.
- A filter that carries a fixed amount of text from the previous chunk, but has already sent
  the start of the value.
- A hold-back shorter than the pattern, a missing flush at the end of the stream, and output
  that is corrupted rather than leaked.

## What it does not catch

It tests a filter you can call in your own code. To test a running gateway, where the model,
the network and the SSE parser decide the chunks, use the
[leak benchmark](conformance/index.md).

The full API, async support and Hypothesis strategies are in the
[Python README](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/chunk-invariance) and
the [TypeScript README](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/chunk-invariance-js).
Why this class of bug exists, and where it was found: [split-boundary leaks](split-boundary-leaks.md).
