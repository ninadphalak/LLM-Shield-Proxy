# Supported PII & Sensitive Data Types

[⬅️ Back to Features Catalog](/docs/features-overview)

The LLM-Shield-Proxy employs a **3-Tier Cascade Engine** to detect and redact sensitive data. Below is an exhaustive list of the data types natively detected by Tiers 1 and 2, along with representative samples of semantic types covered by Tier 3 NLP models.

---

## 🛡️ Tier 1: Pre-Compiled Structured Patterns
Tier 1 uses pre-compiled `google-re2` regular expressions to detect structured formats with RE2's bounded-time matching model.

All ten native pattern names below have focused redaction, non-disclosure, and rehydration tests. That is pattern-path coverage, not a population-level precision or recall claim. The expressions match the documented shapes; they do not validate whether an identifier was actually issued.

**Native Tier 1 pattern catalog:**
1. **`CREDIT_CARD`**: 13-16 digits with optional spaces or hyphens. It does not run a Luhn check or identify an issuer.
2. **`SSN`**: US Social Security Numbers (e.g., `XXX-XX-XXXX`).
3. **`EMAIL`**: Standard email addresses.
4. **`PHONE`**: Selected 7- or 10-digit domestic shapes with optional country prefix and common separators. Extensions are not part of the native expression.
5. **`IP_ADDRESS`**: IPv4 network addresses.
6. **`AWS_API_KEY`**: AWS `AKIA`/`ASIA` access-key shapes and the existing `sk-` API-key shape.
7. **`GITHUB_PAT`**: GitHub Personal Access Tokens (e.g., `ghp_...`).
8. **`SSH_PRIVATE_KEY`**: Private-key PEM/OpenSSH header text; the native expression does not parse or validate the key body.
9. **`JWT_TOKEN`**: Three-segment JWT-shaped strings; signatures and claims are not validated.
10. **`MRN`**: The project-specific `NNN-NN-NNX` medical-record-number shape.

> [!TIP]
> You can easily add your own Tier 1 detectors using the [Bring Your Own Regex (BYOR)](bring-your-own-regex-byor-custom-rules.md) feature via `policies.yaml`.

---

## 🧠 Tier 2: Shannon Entropy (Unstructured Secrets & Cryptography)
Because developers often leak proprietary API keys or database passwords that don't match a standard Regex format, Tier 2 acts as a mathematical dragnet.

**What gets detected:**
- **Cryptographic Keys & Salts**: High-entropy strings exceeding `\tau_H \ge 4.5` bits/symbol.
- **Obfuscated / Smuggled Data**: Text-sized Base64 candidates and Hex-encoded secrets. Base64 decoding is bounded to 8,192 characters; images and larger encoded interiors are outside this detector's scope.
- **Secret candidates**: Configured high-entropy token shapes above the selected length and entropy thresholds. Entropy is a heuristic and can produce false positives and false negatives.

---

## 🤖 Tier 3: NLP NER Models (Semantic & Contextual Data)
Tier 3 utilizes Named Entity Recognition (NER) models (like Presidio or local ONNX runtimes) to understand the semantic meaning of low-entropy text.

Tiers 1 and 2 target configured structured formats and high-entropy candidates; both can produce false positives and false negatives. Tier 3 can add contextual entity detection, with quality depending on the selected model, thresholds, language, and evaluation corpus.

### Standard PII (General NER)
- **`PERSON`**: Full names, patient names, employee names.
- **`ORGANIZATION`**: Company names, hospitals, government agencies.
- **`LOCATION`**: Physical addresses, cities, states, zip codes.
- **`DATE_TIME`**: Specific dates of birth, admission dates, meeting times.

### Domain-Specific Models
If you deploy domain-specific NER models via the Proxy's ONNX runtime, the following domain-specific entities can be redacted for compliance:

#### 🏥 HIPAA & Healthcare (Clinical NER)
- **`PHI_DIAGNOSIS`**: Medical conditions, ICD-10 codes.
- **`PHI_MEDICATION`**: Prescriptions, dosages.
- **`PHI_TREATMENT`**: Surgical procedures, therapy notes.
- **`HEALTH_PLAN_BENEFICIARY`**: Insurance group numbers, Medicaid IDs.

#### 🏦 SOC 2, PCI-DSS & Financial (Financial NER)
- **`BANK_ACCOUNT`**: Checking/Savings account numbers.
- **`ROUTING_NUMBER`**: ABA Routing numbers.
- **`SWIFT_BIC`**: International bank identifiers.
- **`FINANCIAL_TRANSACTION`**: Specific ledger amounts or invoice numbers linked to an entity.

---
**Next Steps:** Learn how the Proxy substitutes this detected data with synthetic equivalents in the [Format-Preserving Synthetic Masking](format-preserving-synthetic-masking-entropy.md) guide.
