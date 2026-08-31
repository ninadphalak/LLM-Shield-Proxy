# Format-Preserving Synthetic Masking & Entropy

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**Format-Preserving Synthetic Masking** is the proxy's default Data Loss Prevention (DLP) substitution strategy. Instead of replacing sensitive data with structural tags (e.g., turning a name into `[PERSON_1]`), it deterministically replaces the data with a realistic, unbracketed synthetic entity (e.g., turning "John Doe" into "Michael Smith", or a real SSN into a validly formatted fake SSN).

> [!TIP]
> **Wondering what specific types of data are detected?** Check out the [Supported PII & Sensitive Data Types](supported-pii-types.md) feature guide for an exhaustive list.

## How It Works
Traditional structural tagging damages the performance of Large Language Models in two critical ways:
1. **Grammatical Damage:** Bracketed tags `[LIKE_THIS_1]` disrupt the natural language attention weights of transformer models, degrading the quality of the LLM's reasoning and generation.
2. **BPE Token Bloat:** Byte-Pair Encoding tokenizers split brackets and underscores into multiple tokens, increasing the cost of the prompt and slowing down generation.

LLM-Shield-Proxy solves this utilizing robust canonical locale substitution combined with deterministic hashing:
1. **Deterministic Mapping:** Within the documented mapping scope, the same detected value is intended to receive the same substitute. Verify scope and multi-replica behavior for the selected vault mode.
2. **Coherent Substitution:** Rather than generic structural strings, the underlying generation logic respects mathematical formats and regional locales:
   - **Credit Cards:** A real Visa card number is swapped with a validly checksummed (Luhn algorithm) synthetic Visa card number.
   - **Emails:** A real email like `alex.smith@company.com` is swapped with a syntactically correct placeholder like `johndoe@fictional.net`.
   - **SSNs / Phone Numbers:** A detected value can be replaced with a format-aware synthetic value. A realistic format does not make the substitute a valid or issued identifier.
   *(Tier 1/2 target configured structured formats and entropy candidates; semantic entities such as personal names require an enabled and validated [Tier 3 NLP model](../../deployment.md#advanced-feature-flags-compliance-security-and-engineering). Each tier can produce false positives and false negatives.)*
3. **Supported rehydration:** When a returned synthetic token matches a retained mapping on the inspected response path, the sliding window can replace it with the original value. Transformed, truncated, or out-of-scope output may not match.


```mermaid
flowchart LR
    A[Real Data: John] --> B(Deterministic Seed)
    B --> C(Canonical Locale Generator)
    C --> D[Synthetic Data: Michael]
    D --> E[Egress to LLM]
```


View diagram on GitHub mobile 📱 -->


## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_SYNTHETIC_SWAPPING` | Toggles between Synthetic Masking (`true`) and Structural Tagging (`false`). | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Referential Consistency:** Repeated values can map to the same substitute within the configured scope. That can preserve some relationships, but it does not establish unchanged model reasoning or output quality.
* **Stream fragmentation:** Because synthetic substitutes are unbracketed, ambiguous overlaps and collisions require explicit fixtures. The buffer tests registered mappings across fragmented delivery; it does not establish universal natural-language matching.

## FAQ

**Q: Can I turn off synthetic masking and use standard bracket tags for auditing?**
A: Set `ENABLE_SYNTHETIC_SWAPPING=false` to select structural tags such as `[PERSON_1]`. Confirm the effective setting at startup and test client/model handling of those tags.

**Q: Does generating synthetic data slow down the request?**
A: No. The proxy caches the generated synthetic entities in the active session's memory vault, meaning the substitution generator is only invoked once per unique entity, keeping latency near zero.


## Plainspeak
This feature creates realistic fake data to replace sensitive information.

Short structural markers and realistic-looking substitutes can affect models differently. Synthetic mode is intended to retain some surface format, but accuracy, privacy, token use, and task quality must be compared on representative fixtures.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_pii_engine.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_pii_engine.py).
