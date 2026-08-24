# Tier 2 Shannon Entropy Scanner

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
The **Tier 2 Shannon Entropy Scanner** is the second defensive layer in the LLM-Shield-Proxy. While regex (Tier 1) excels at structured data like SSNs, it completely fails to detect unstructured, proprietary cryptographic secrets (e.g., custom JWTs, internal API tokens, Database connection passwords). The Tier 2 engine automatically flags and redacts these unstructured secrets by evaluating the mathematical information density (entropy) of incoming token streams.

## How It Works
Instead of relying on massive, slow dictionaries of potential secret formats, the proxy applies Information Theory. 

1. **Sliding Window Evaluation:** The engine runs a vectorized O(N) math loop that evaluates the bit density of text using Shannon's Entropy formula: $H(S) = -\sum p(c) \log_2 p(c)$.
2. **Algorithmic Thresholds:** It isolates high-density character strings and evaluates them against strict thresholds. It targets Base64 strings with an entropy $\ge 4.5$ bits/char and Hexadecimal strings with $\ge 3.4$ bits/char.
3. **Microsecond Execution:** Because it avoids heavy regex backtracking and uses native math operations, the entire scan executes in `<6 µs`.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart TD
    A[Payload Stream] --> B(Extract Base64 & Hex Candidates)
    B --> C{Calculate H(S)}
    C -->|>= 4.5 Bits/Char| D[Identify as Secret]
    C -->|< 4.5 Bits/Char| E[Identify as Safe Text]
    D --> F[Redact / Route to Vault]
```
-->

View diagram on GitHub mobile 📱 -->
![Tier 2 Architecture](../images/tier-2-shannon-entropy-scanner.svg)

## Performance Profile
- **Execution Speed:** `<6 µs` per evaluation chunk.
- **Overhead:** Extremely low. The math-bound loop bypasses the Python GIL using vectorized operations.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_TIER2_ENTROPY` | Toggles the Shannon Entropy scanner on or off. Defaults to `true`. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **False Positive Prevention:** Standard English text inherently has lower entropy than cryptographic secrets. The thresholds (4.5 and 3.4) are mathematically tuned to prevent standard conversational text from being flagged as a secret.
* **Base64 Candidate Inspection:** The engine safely extracts Base64 candidate strings (≥ 20 characters) and inspects them recursively to neutralize obfuscated PII payloads before they bypass standard filters.

## FAQ

**Q: Why use entropy instead of a massive Regex dictionary for secrets?**
A: Regex dictionaries for secrets require evaluating hundreds of patterns (AWS keys, GCP keys, Stripe keys, etc.). This introduces severe latency and backtracking overhead, and it will still miss internal, proprietary keys. Entropy instantly catches *any* high-density secret mathematically, regardless of the vendor.

**Q: Will this accidentally redact normal words or long URLs?**
A: No. Standard English and typical URLs do not contain the random character distribution required to trip the $\ge 4.5$ bits/char Base64 threshold. 


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_pii_engine.py`](../../../tests/test_pii_engine.py).
