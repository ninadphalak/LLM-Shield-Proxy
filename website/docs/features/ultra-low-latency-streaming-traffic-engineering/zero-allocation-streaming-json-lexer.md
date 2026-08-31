# Bounded Streaming JSON Lexer

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Streaming JSON Lexer** parses incremental response events without retaining the complete response history. It uses `orjson` for JSON serialization and parsing where that code path applies.

## How It Works
Traditional HTTP proxies load entire JSON bodies into memory, converting them into massive Python dictionaries. Under high concurrency, this causes massive spikes in the Resident Set Size (RSS) and forces the Python Garbage Collector to freeze the event loop.

1. **Native JSON implementation:** The lexer uses `orjson` on supported parsing paths.
2. **Bounded state:** Incremental parsing avoids retaining the full response history; allocations still occur and are measured by the conformance benchmark.
3. **SSE chunk parsing:** On supported paths, the lexer identifies `data:` content and processes fragments while retaining a bounded trailing window rather than the complete response history.


```mermaid
flowchart TD
    A[Raw TCP Socket] --> B(orjson Rust Lexer)
    B --> C(Parse JSON Frame)
    C --> D[C/Rust Memory Space]
    D --> E[Python Redaction Generator]
    E --> F[Garbage Collection Bypassed]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Execution speed:** Uses native `orjson` parsing; comparative throughput depends on payload and environment.
- **Memory behavior:** Bounded parser state prevents retained input from growing with stream duration. Process RSS and concurrency capacity require the published service-level protocol.

## Configuration Flags
The lexer is deeply embedded into the proxy's core and operates automatically without specific configuration flags.

## Critical Logic & Edge Cases
* **Invalid payload handling:** Malformed JSON can be rejected on paths that parse a complete JSON value. Confirm status codes and buffering behavior for request bodies, SSE fragments, and truncated streams separately.
* **Numeric limits:** Exercise unusually large integers, floats, nesting, and payload sizes against both the parser and the surrounding application limits; native parsing does not remove resource-exhaustion risk.

## FAQ

**Q: Why is bypassing the Python GIL so important?**
A: In asynchronous Python (`asyncio`), CPU-heavy parsing can block other work on the same event loop. Native parsing can reduce that cost, but concurrency capacity must be measured for the complete service and workload.

**Q: Are there any compatibility issues with `orjson`?**
A: JSON objects require string keys, but integration limits also include supported SSE framing, maximum line size, Unicode handling, nesting, provider-specific envelopes, and incomplete streams. Exercise the conformance fixtures and provider-specific tests.

**Q: Does this help protect against Denial of Service?**
A: Bounded parser state reduces one memory-growth risk, but it does not guarantee that a pod cannot run out of memory. Enforce payload and concurrency limits and validate peak RSS under load.


## Plainspeak
This feature is an incremental data reader designed to limit retained state.

Instead of retaining the complete response, it keeps only the state needed for the current parsing decision. It still allocates memory, so the conformance and service benchmarks report that behavior rather than calling it allocation-free.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_streaming_json_lexer.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_streaming_json_lexer.py).
