# Tier 2 Shannon Entropy Scanner

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Tier 2 Shannon Entropy Scanner** is the second defensive layer in the LLM-Shield-Proxy. While regex (Tier 1) excels at structured data like SSNs, it completely fails to detect unstructured, proprietary cryptographic secrets (e.g., custom JWTs, internal API tokens, Database connection passwords). The Tier 2 engine automatically flags and redacts these unstructured secrets by evaluating the mathematical information density (entropy) of incoming token streams.

## How It Works
Instead of relying on massive, slow dictionaries of potential secret formats, the proxy applies Information Theory.

1. **Sliding Window Evaluation:** The engine runs a vectorized O(N) math loop that evaluates the bit density of text using Shannon's Entropy formula: `H(S) = -\sum p(c) \log_2 p(c)`.
2. **Algorithmic Thresholds:** It isolates high-density character strings and evaluates them against strict thresholds. It targets Base64 strings with an entropy `\ge 4.5` bits/char and Hexadecimal strings with `\ge 3.4` bits/char.
3. **Scoped Measurement:** Benchmark the scanner separately from the complete request path, using the intended chunk-size and input distribution.


```mermaid
flowchart TD
    A[Payload Stream] --> B(Extract Base64 & Hex Candidates)
    B --> C(Calculate H(S))
    C -->|>= 4.5 Bits/Char| D[Identify as Secret]
    C -->|< 4.5 Bits/Char| E[Identify as Safe Text]
    D --> F[Redact / Route to Vault]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Extremely low. The math-bound loop bypasses the Python GIL using vectorized operations.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_TIER2_ENTROPY` | Toggles the Shannon Entropy scanner on or off. Defaults to `true`. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **False Positive Prevention:** Standard English text inherently has lower entropy than cryptographic secrets. The thresholds (4.5 and 3.4) are mathematically tuned to prevent standard conversational text from being flagged as a secret.
* **Base64 Candidate Inspection:** The engine extracts candidates of at least 20 characters and decodes text-sized values up to 8,192 characters. Larger encoded interiors are skipped to bound detector work; a 256-character guard on each boundary keeps adjacent plaintext in scope.

## FAQ

**Q: Why use entropy instead of a massive Regex dictionary for secrets?**
A: Regex dictionaries can miss proprietary key formats. Entropy adds a format-independent signal for sufficiently long, high-density candidates, but its effectiveness depends on the configured threshold and input distribution.

**Q: Will this accidentally redact normal words or long URLs?**
A: False positives are possible with any heuristic. The default threshold reduces matches on ordinary prose, but deployments should validate URLs, identifiers, and domain-specific text against their own corpus.


## Plainspeak
This feature acts like a randomness detector. While some sensitive information (like phone numbers) has a predictable format, things like passwords or secret API keys just look like random gibberish.

Because we can't search for a specific password pattern, this scanner mathematically measures how "random" a piece of text is (known as Shannon entropy). If it spots a string of text that is completely unpredictable and random, it flags it as a likely secret key and hides it to prevent accidental leaks.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_pii_engine.py`](https://github.com/YOUR_ORG/LLM-Shield-Proxy/blob/main/tests/test_pii_engine.py).
