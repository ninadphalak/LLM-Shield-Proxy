# Tier 2 Shannon Entropy Scanner

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Tier 2 Shannon Entropy Scanner** identifies unstructured secret candidates (like cryptographic keys, salts, or obfuscated tokens) by measuring their mathematical randomness. It catches high-entropy secrets that lack a known prefix, acting as a fallback for the rigid patterns of Tier 1.

## How It Works
The scanner extracts potential candidates (Base64 or Hex strings) and evaluates their Shannon entropy.

1. **Candidate Scoring:** The engine calculates character entropy using the formula `H(S) = -∑ p(c) log₂ p(c)`.
2. **Thresholds:** It compares the score against configured limits. By default, it flags Base64 strings at `≥ 4.5` bits/character and Hex strings at `≥ 3.4` bits/character.
3. **Bounded Decoding:** To prevent DoS via massive encoded payloads, Base64 candidate inspection is bounded. It extracts candidates over 20 characters and decodes strings up to 8,192 characters.

```mermaid
flowchart TD
    A[Payload Stream] --> B(Extract Base64 & Hex Candidates)
    B --> C{Calculate H(S)}
    C -->|>= 4.5 Bits/Char| D[Identify as Secret]
    C -->|< 4.5 Bits/Char| E[Identify as Safe Text]
    D --> F[Redact / Route to Vault]
```

## Configuration Flags

| Environment Variable | Description | Linked Guide |
| :--- | :--- | :--- |
| `ENABLE_TIER2_ENTROPY` | Toggles the Shannon Entropy scanner on or off. Defaults to `true`. | [View in deployment.md](/docs/deployment) |

## Implementation Details & Edge Cases
* **False-Positive Trade-offs:** Entropy thresholds are heuristics, not guarantees. A sufficiently random identifier might be falsely flagged, while a low-entropy password (e.g., `password123`) will be missed. You must evaluate these thresholds against representative traffic.
* **Payload Guards:** The engine imposes a 256-character guard on boundaries to ensure adjacent plaintext remains in scope while decoding.

## FAQ

**Q: Why use entropy instead of a massive regex dictionary for secrets?**
A: Regex dictionaries cannot anticipate every proprietary key format or newly generated token. Entropy provides a format-independent signal that captures high-density secrets regardless of their specific shape or origin.

**Q: Will this accidentally redact normal words or long URLs?**
A: The default threshold is tuned to avoid matching ordinary prose or standard URLs. However, deployments should still validate this behavior against domain-specific text to tune the thresholds if necessary.

## Practical Effect
Tier 2 dynamically detects unstructured secrets that evade standard pattern matching. While highly effective against cryptographic material, it can produce both false positives and false negatives due to its heuristic nature.

## Related Tests
Tests: [`tests/test_pii_engine.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_pii_engine.py).
