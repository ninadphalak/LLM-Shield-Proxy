# Supported PII & Sensitive Data Types

[⬅️ Back to Features Catalog](/docs/features-overview)

LLM-Shield-Proxy uses three detector tiers. Tiers 1 and 2 have the built-in patterns and
heuristics listed below. Tier 3 uses an operator-supplied model, so its entity types depend on
that model.

---

## 🛡️ Tier 1: Pre-Compiled Structured Patterns
Tier 1 uses pre-compiled `google-re2` regular expressions to detect structured formats with RE2's bounded-time matching model.

All ten native pattern names below have focused redaction, non-disclosure, and rehydration tests. That is pattern-path coverage, not a population-level precision or recall claim. The expressions match the documented shapes; structural signals below do not establish whether an identifier was actually issued.

**Native Tier 1 pattern catalog:**
1. **`CREDIT_CARD`**: 13-16 digits with optional spaces or hyphens. Every matching run is redacted. Selected issuer prefixes and the Luhn checksum contribute only to an internal confidence value; neither can drop a match. A finite issuer table cannot exclude private-label, gift, or newly assigned cards, and a one-digit error or transposition can make a genuine card fail Luhn. See [validation as a signal](#validation-is-a-signal-not-a-gate).
2. **`SSN`**: US Social Security Number shapes such as `XXX-XX-XXXX`. The detector does not
   validate issuance. A general-ledger code with the same shape is also redacted because the
   pattern cannot distinguish it from an SSN.
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

**Measured false positives.** On a 22-string corpus of ordinary business text, the detector
matched **17 strings and 18 spans (77.3% of strings)**. The corpus includes order numbers,
invoice IDs, SKUs, ISBNs, tracking numbers, ledger codes, cost centres, and dates.

Using issuer tables and Luhn to reject card-shaped matches reduced the count to 11 strings, but
it also missed a private-label-shaped value after a digit transposition. Rejecting bare long
phone matches also missed plausible international numbers. Those rejection rules were not used.

As a result, the detector keeps known false positives. A `ddd-dd-dddd` ledger code has the same
shape as an SSN. A 13--16 digit business identifier can have the same shape as a private-label,
gift, new, or mistyped card number. The pattern alone cannot safely tell them apart.

**Confidence is internal only.** The validator computes `high` or `medium`, but does not attach
that value to the detected span, audit record, or OpenTelemetry span. The detection path receives
only the decision to keep the match. If validation raises an exception, the match is kept.

> [!TIP]
> You can easily add your own Tier 1 detectors using the [Bring Your Own Regex (BYOR)](bring-your-own-regex-byor-custom-rules.md) feature via `policies.yaml`.

---

## 🧠 Tier 2: Shannon Entropy (Unstructured Secrets & Cryptography)
Tier 2 checks selected token candidates that may not match a known key prefix or structured
pattern.

**What gets detected:**
- **Cryptographic Keys & Salts**: High-entropy strings exceeding `\tau_H \ge 4.5` bits/symbol.
- **Obfuscated / Smuggled Data**: Text-sized Base64 candidates and Hex-encoded secrets. Base64 decoding is bounded to 8,192 characters; images and larger encoded interiors are outside this detector's scope.
- **Secret candidates**: Configured high-entropy token shapes above the selected length and entropy thresholds. Entropy is a heuristic and can produce false positives and false negatives.

---

## 🤖 Tier 3: NLP NER Models (Semantic & Contextual Data)
Tier 3 runs a compatible local ONNX Named Entity Recognition (NER) model. The model uses nearby
text to classify configured entity types.

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
