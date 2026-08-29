# Supported PII & Sensitive Data Types

[⬅️ Back to Features Catalog](/docs/features-overview)

The LLM-Shield-Proxy employs a **3-Tier Cascade Engine** to detect and redact sensitive data. Below is an exhaustive list of the data types natively detected by Tiers 1 and 2, along with representative samples of semantic types covered by Tier 3 NLP models.

---

## 🛡️ Tier 1: Microsecond Regex (High-Fidelity Structured Data)
Tier 1 utilizes zero-allocation, pre-compiled `google-re2` DFA regular expressions to detect highly structured, standardized formats in linear O(N) time.

**Exhaustive List of Tier 1 Native Detectors:**
1. **`CREDIT_CARD`**: Major credit card formats (Visa, MasterCard, Amex, Discover) validated via length and spacing.
2. **`SSN`**: US Social Security Numbers (e.g., `XXX-XX-XXXX`).
3. **`EMAIL`**: Standard email addresses.
4. **`PHONE`**: International and domestic phone numbers, including extensions.
5. **`IP_ADDRESS`**: IPv4 network addresses.
6. **`AWS_API_KEY`**: Amazon Web Services Access Keys (e.g., `AKIA...`, `ASIA...`, `sk-...`).
7. **`GITHUB_PAT`**: GitHub Personal Access Tokens (e.g., `ghp_...`).
8. **`SSH_PRIVATE_KEY`**: RSA/DSA/ECDSA/Ed25519 private keys (detects PEM headers).
9. **`JWT_TOKEN`**: JSON Web Tokens.
10. **`MRN`**: Medical Record Numbers (Standardized formats).

> [!TIP]
> You can easily add your own Tier 1 detectors using the [Bring Your Own Regex (BYOR)](bring-your-own-regex-byor-custom-rules.md) feature via `policies.yaml`.

---

## 🧠 Tier 2: Shannon Entropy (Unstructured Secrets & Cryptography)
Because developers often leak proprietary API keys or database passwords that don't match a standard Regex format, Tier 2 acts as a mathematical dragnet.

**What gets detected:**
- **Cryptographic Keys & Salts**: High-entropy strings exceeding `\tau_H \ge 4.5` bits/symbol.
- **Obfuscated / Smuggled Data**: Base64 or Hex-encoded payloads injected into the prompt.
- **Proprietary Tokens**: Any alphanumeric string of sufficient length (16+ characters) that is mathematically random enough to be classified as a secret.

---

## 🤖 Tier 3: NLP NER Models (Semantic & Contextual Data)
Tier 3 utilizes Named Entity Recognition (NER) models (like Presidio or local ONNX runtimes) to understand the semantic meaning of low-entropy text.

While Tiers 1 and 2 detect *structured* data perfectly, Tier 3 is required for *contextual* data (where the words look like normal English but hold sensitive meaning).

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
