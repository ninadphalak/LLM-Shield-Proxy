# Supported PII & Sensitive Data Types

[⬅️ Back to Features Catalog](/docs/features-overview)

LLM-Shield-Proxy uses a three-tier detection cascade. Tiers 1 and 2 rely on built-in structural patterns and entropy heuristics. Tier 3 utilizes an operator-supplied NER model, meaning its supported entities depend entirely on the model you deploy.

---

## 🛡️ Tier 1: Pre-Compiled Structured Patterns
Tier 1 uses highly optimized `google-re2` regular expressions to detect deterministic, structured data formats. These patterns flag structural shapes; they do not validate if an identifier (like a credit card or SSN) was actually issued or is active.

**Native Tier 1 Catalog:**
1. **`CREDIT_CARD`**: 13-19 digits with optional spaces or hyphens. Every matching run is redacted. While Luhn checksums and issuer prefixes are evaluated, they are only used for internal confidence scoring-a typo in a real card could fail Luhn, so we redact the shape regardless to prevent leaks. Note: a redaction span is never allowed to stop mid-way through a run of digits; it grows to cover the whole identifier.
2. **`SSN`**: US Social Security Number shapes (e.g., `XXX-XX-XXXX`).
3. **`EMAIL`**: Standard email addresses. Domains are not validated against public-suffix lists, so non-routable domains will still match.
4. **`PHONE`**: Standard 7- or 10-digit domestic shapes with optional country codes.
5. **`IP_ADDRESS`**: Standard IPv4 network addresses.
6. **`AWS_API_KEY`**: AWS `AKIA`/`ASIA` access-key shapes, and standard `sk-` API keys.
7. **`GITHUB_PAT`**: GitHub Personal Access Tokens (e.g., `ghp_...`).
8. **`SSH_PRIVATE_KEY`**: Private-key PEM/OpenSSH header boundaries.
9. **`JWT_TOKEN`**: Three-segment JWT-shaped strings (signatures/claims are not cryptographically validated).
10. **`MRN`**: Project-specific Medical Record Number shapes (e.g., `NNN-NN-NNX`).

### Validation is a Signal, Not a Gate
Tier 1 structural checks are confidence signals, not definitive gates. A ledger code formatted as `ddd-dd-dddd` will trigger the SSN redaction rule. A 16-digit order number will trigger the Credit Card rule. The pattern alone cannot safely distinguish them.

The proxy intentionally errs on the side of false positives (over-redacting) rather than false negatives (leaking data). 

> [!TIP]
> You can easily add your own Tier 1 detectors using the [Bring Your Own Regex (BYOR)](bring-your-own-regex-byor-custom-rules.md) feature via `policies.yaml`.

---

## 🧠 Tier 2: Shannon Entropy (Unstructured Secrets & Cryptography)
Tier 2 identifies token candidates that do not match known Tier 1 patterns but exhibit the high mathematical randomness typical of secrets.

**What gets detected:**
- **Cryptographic Keys & Salts**: High-entropy strings exceeding 4.5 bits per symbol.
- **Obfuscated / Smuggled Data**: Text-sized Base64 blobs and Hex-encoded secrets. Base64 decoding is bounded to 8,192 characters to prevent DoS attacks via massive images.
- **Secret Candidates**: Any high-entropy token shapes exceeding configured thresholds. Entropy is a heuristic and will produce false positives on randomly generated IDs.

---

## 🤖 Tier 3: NLP NER Models (Semantic & Contextual Data)
Tier 3 runs a local, operator-supplied ONNX Named Entity Recognition (NER) model. This tier uses surrounding sentence context to identify unstructured semantic entities (like names or locations) that lack fixed regex patterns.

### Strict Fallback Policy
**There is no regex fallback for Tier 3.** If no ONNX model is loaded, the proxy will not attempt to guess names or organizations. Attempting to approximate names using capitalization rules (e.g., "Any Capitalized Phrase") results in catastrophic false-positive rates that destroy prompt grammar and context.

If a profile requests Tier 3 entities but no model is loaded, the proxy logs a warning at startup and reports `"name_redaction": "unavailable"` in the `/health` endpoint.

### Standard PII (General NER)
Depending on your model, common entities include:
- **`PERSON`**: Full names, patient names, employee names.
- **`ORGANIZATION`**: Company names, hospitals, government agencies.
- **`LOCATION`**: Physical addresses, cities, states, zip codes.
- **`DATE_TIME`**: Specific dates of birth, admission dates, meeting times.

### Domain-Specific Models
If you deploy domain-specific NER models via the Proxy's ONNX runtime, you can redact specialized data:

#### 🏥 HIPAA & Healthcare (Clinical NER)
- **`PHI_DIAGNOSIS`**: Medical conditions, ICD-10 codes.
- **`PHI_MEDICATION`**: Prescriptions, dosages.
- **`PHI_TREATMENT`**: Surgical procedures, therapy notes.
- **`HEALTH_PLAN_BENEFICIARY`**: Insurance group numbers, Medicaid IDs.

#### 🏦 SOC 2, PCI-DSS & Financial (Financial NER)
- **`BANK_ACCOUNT`**: Checking/Savings account numbers.
- **`ROUTING_NUMBER`**: ABA Routing numbers.
- **`SWIFT_BIC`**: International bank identifiers.

---
**Next Steps:** Learn how the Proxy substitutes this detected data with synthetic equivalents in the [Format-Preserving Synthetic Masking](format-preserving-synthetic-masking-entropy.md) guide.
