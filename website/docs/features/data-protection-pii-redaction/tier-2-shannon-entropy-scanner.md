# Tier 2 Shannon Entropy Scanner

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Tier 2 Shannon Entropy Scanner** complements configured patterns by scoring selected token candidates with Shannon entropy. It can identify some high-entropy secret shapes that have no known prefix, but it can also miss low-entropy secrets and flag benign identifiers.

## How It Works
The scanner calculates Shannon entropy for selected Base64- and hexadecimal-shaped candidates.

1. **Candidate scoring:** The engine calculates character entropy with
   `H(S) = -\sum p(c) \log_2 p(c)`.
2. **Thresholds:** It compares candidates with the configured thresholds. The defaults target
   Base64 at `\ge 4.5` bits per character and hexadecimal at `\ge 3.4`.
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
- **Overhead:** Cost depends on candidate count, length, decoding, runtime, and concurrency.
  Measure it with representative input.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_TIER2_ENTROPY` | Toggles the Shannon Entropy scanner on or off. Defaults to `true`. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **False-positive trade-off:** The configured thresholds are heuristics. Evaluate them on representative secrets, identifiers, natural language, encoded content, and hard negatives before deployment.
* **Base64 Candidate Inspection:** The engine extracts candidates of at least 20 characters and decodes text-sized values up to 8,192 characters. Larger encoded interiors are skipped to bound detector work; a 256-character guard on each boundary keeps adjacent plaintext in scope.

## FAQ

**Q: Why use entropy instead of a massive Regex dictionary for secrets?**
A: Regex dictionaries can miss proprietary key formats. Entropy adds a format-independent signal for sufficiently long, high-density candidates, but its effectiveness depends on the configured threshold and input distribution.

**Q: Will this accidentally redact normal words or long URLs?**
A: False positives are possible with any heuristic. The default threshold reduces matches on ordinary prose, but deployments should validate URLs, identifiers, and domain-specific text against their own corpus.


## Practical effect
The scanner computes Shannon entropy for selected candidates and flags values above configured thresholds. High entropy is neither necessary nor sufficient for a secret, so the tier can miss secrets and flag benign data.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_pii_engine.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_pii_engine.py).
