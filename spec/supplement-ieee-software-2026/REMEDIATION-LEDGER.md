# September 17-18 2026 remediation ledger

This is the initial 29-entry remediation list referenced in the article's Limits and
Disclosure section as "28 of 29 entries as defects". Entry 28 was examined and classified
NOT A DEFECT; the remaining 28 were treated as defects and closed.

All 29 rows were closed before the LLM-Shield-Proxy 1.6.6 release of 2026-09-18
(PyPI `llm-shield-proxy` 1.6.6, tag `v1.6.6`). Four further issues found outside this list
during the same work are noted below the table. Fixes landed through public pull requests
#38-#45 in this repository, where the code, tests and review history can be read.

`Severity` is the author's triage label at the time of entry, not a CVSS score. No CVE was
requested; these are defects in the author's own unreleased-to-that-point response-path
work and in shipped code, disclosed here for reviewer scrutiny rather than as an advisory.

## The 29 entries

The `State` and `Branch / PR` columns are reproduced verbatim from the working ledger and
record the value each row carried while the work was in progress -- some rows still read
"PR open". That is the historical record, not the final state. **Every row was closed before
the 1.6.6 release**: 28 fixed, entry 28 classified NOT A DEFECT. The columns are left
unedited so the reproduced table matches the private working record it came from.

| # | Defect | Severity | State | Branch / PR |
|---|---|---|---|---|
| 1 | EMAIL pattern quadratic backtracking | High | **MERGED** | #39 |
| 2 | Percent-encoded PII invisible | High | **PR open 5/5** | #38 |
| 3 | Value split across events in a sibling field | High | **PR open 5/5** | #38 |
| 4 | `resources/read` skips SSRF gate and RBAC | **Critical** | **PR #40 open** | fix/mcp-resources-read-ssrf |
| 5 | Sibling `params` never URL-checked | High | **PR #40 open** | fix/mcp-resources-read-ssrf |
| 6 | Reasoning-model deltas never scanned | **Critical** | **DONE** | fix/sse-dispatcher-scan-by-default |
| 7 | List-valued content parts never scanned | **Critical** | **DONE** | same |
| 8 | `data:` without a space bypasses everything | **Critical** | **DONE** | same |
| 9 | Truncated final line forwarded raw | High | **DONE** | same |
| 10 | Anthropic `message_start` not handled | High | **DONE** | same |
| 11 | Legacy `text` choices not handled | Medium | **DONE** | same |
| 12 | Canary abort still flushes the raw partial line | High | **DONE** | fix/sse-dispatcher-scan-by-default |
| 13 | Vault stand-ins are an unkeyed hash of the plaintext | **Critical** | **DONE** | fix/sse-dispatcher-scan-by-default |
| 14 | 20+ digit card run missed entirely (PCI) | **Critical** | **DONE** | same |
| 15 | Non-ASCII dashes defeat SSN / card / phone | High | **DONE** | same |
| 16 | Response path never normalizes (zero-width, fullwidth) | High | **DONE** | fix/sse-dispatcher-scan-by-default |
| 17 | `anthropic_tool_ordinals` unbounded (46 MB/stream) | High | **DONE** | same |
| 18 | `egress_mode` typo silently fails open | High | **DONE** | same |
| 19 | Tier 2 `\b` misses secrets glued to CJK text | Medium | **DONE** | fix/sse-dispatcher-scan-by-default |
| 20 | Checked URL is not the forwarded URL | High | **DONE** | fix/sse-dispatcher-scan-by-default |
| 21 | Stand-in collisions rehydrate the wrong person | Medium | **DONE** | same |
| 22 | One malformed line kills the whole stream | Medium | **DONE** (restated) | same |
| 23 | Base64: unpadded and urlsafe never decoded | Medium | **DONE** | fix/sse-dispatcher-scan-by-default |
| 24 | HTML entities never decoded | Low | **DONE** | fix/sse-dispatcher-scan-by-default |
| 25 | Homoglyph domains missed | Medium | **DONE** | fix/sse-dispatcher-scan-by-default |
| 26 | Invisible chars outside the strip class survive | Medium | **DONE** | fix/sse-dispatcher-scan-by-default |
| 27 | Oversized base64 skipped rather than edge-scanned | Medium | **DONE** | fix/sse-dispatcher-scan-by-default |
| 28 | `PAYLOAD_MAX_REDACT_STRING_LENGTH` forwards long strings raw | Medium | **NOT A DEFECT** (see batch six) | |
| 29 | Double encoding not iterated | Low | **DONE** | same |

## Found during the same work, outside the 29-entry list

- Lexer ReDoS.
- A capacity bypass forgiven by `FAIL_OPEN`.
- An audit-worker hang.
- Two latent `NameError`s.

## What this ledger does and does not support

- It supports the sentence "The initial September 17-18 remediation ledger classifies 28 of
  29 entries as defects, including unscanned response carriers absent from this corpus."
- It does **not** establish that the article's corpus covers those carriers. The article says
  so explicitly.
- It does **not** alter the round-8 evidence, which is frozen at tag `v2-evidence-round-8`,
  commit `6cbfee3`, on harness revision 0.2.1 and inspector `94262e29a492ab6a`.
