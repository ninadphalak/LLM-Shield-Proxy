# Supported PII & Sensitive Data Types

[⬅️ Back to Features Catalog](/docs/features-overview)

The LLM-Shield-Proxy employs a **3-Tier Cascade Engine** to detect and redact sensitive data. Below is an exhaustive list of the data types natively detected by Tiers 1 and 2, along with representative samples of semantic types covered by Tier 3 NLP models.

---

## 🛡️ Tier 1: Pre-Compiled Structured Patterns
Tier 1 uses pre-compiled `google-re2` regular expressions to detect structured formats with RE2's bounded-time matching model.

All ten native pattern names below have focused redaction, non-disclosure, and rehydration tests. That is pattern-path coverage, not a population-level precision or recall claim. The expressions match the documented shapes; structural signals below do not establish whether an identifier was actually issued.

**Native Tier 1 pattern catalog:**
1. **`CREDIT_CARD`**: 13-16 digits with optional spaces or hyphens. Every matching run is redacted. Selected issuer prefixes and the Luhn checksum contribute only to an internal confidence value; neither can drop a match. A finite issuer table cannot exclude private-label, gift, or newly assigned cards, and a one-digit error or transposition can make a genuine card fail Luhn. See [validation as a signal](#validation-is-a-signal-not-a-gate).
2. **`SSN`**: US Social Security Numbers (e.g., `XXX-XX-XXXX`). Deliberately unvalidated: a general-ledger code of the same shape is indistinguishable from a real SSN, so both are redacted.
3. **`EMAIL`**: Standard email addresses. The domain is not checked against a public-suffix list, so a reserved or non-routable domain still matches.
4. **`PHONE`**: Selected 7- or 10-digit domestic shapes with optional country prefix and common separators. Extensions are not part of the native expression. Punctuation is not a validation gate: a bare international number can be legitimate, so every native-regex match is redacted.
5. **`IP_ADDRESS`**: IPv4 network addresses.
6. **`AWS_API_KEY`**: AWS `AKIA`/`ASIA` access-key shapes and the existing `sk-` API-key shape.
7. **`GITHUB_PAT`**: GitHub Personal Access Tokens (e.g., `ghp_...`).
8. **`SSH_PRIVATE_KEY`**: Private-key PEM/OpenSSH header text; the native expression does not parse or validate the key body.
9. **`JWT_TOKEN`**: Three-segment JWT-shaped strings; signatures and claims are not validated.
10. **`MRN`**: The project-specific `NNN-NN-NNX` medical-record-number shape.

### Validation is a signal, not a gate

Tier 1 structural checks are confidence signals only. For `CREDIT_CARD`, selected issuer
prefixes and Luhn affect internal confidence, but every 13--16 digit native-regex match is
redacted. `PHONE` has no structural rejection rule: a 12--15 digit international number
may be written without a leading plus or separator. A validator exception keeps the span.

**Measured precision cost.** On a 22-string corpus of ordinary business text — order
numbers, invoice ids, SKUs, ISBNs, tracking numbers, GL codes, cost centres, dates — the
current detector matches something in **17 of 22 strings (77.3%, 18 spans)**. The
structural signals intentionally do not improve that result: dropping card values that
fail both a finite issuer table and Luhn reduced it to 11 of 22, but allowed an
unrecognised private-label shape with a transposed digit to escape redaction. Dropping
bare long phone matches similarly allowed plausible international numbers to escape. The
fail-safe boundary takes precedence over those apparent precision gains.

The false positives are deliberate. `ddd-dd-dddd` ledger codes are structurally identical
to a real SSN. Other 13--16 digit business identifiers are indistinguishable at this
boundary from private-label, gift, newly assigned, or mistyped cards. Narrowing further
would trade a possible disclosure for a cosmetic precision gain.

**What is not implemented:** the validator computes a confidence value
(`high` / `medium`) alongside its keep/drop decision, but that confidence is **not**
currently attached to the span, the audit record, or the OTel span. Only the keep/drop
decision reaches the detection path. If a validator raises for any reason the span is
kept, never dropped.

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
