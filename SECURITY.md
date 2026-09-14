# Security Policy

## Reporting a vulnerability

**Use [GitHub private vulnerability reporting](https://github.com/ninadphalak/LLM-Shield-Proxy/security/advisories/new).**
It is enabled on this repository, the report stays private until a fix is published, and it
needs no email address from either side.

Please do not open a public issue for a vulnerability. Public issues are the right place for
everything else, including benchmark results you disagree with.

Include what you have: affected version, configuration, and the smallest reproduction you
can manage. A report without a reproduction is still worth sending.

### What to expect

This is a single-maintainer project. There is no staffed response rota and no guaranteed
turnaround. Reports are read and acknowledged; a fix timeline depends on severity and on
the maintainer's availability. If that does not meet your disclosure requirements, say so
in the report and coordinate a date rather than assuming one.

## Supported versions

| Distribution | Version | Supported |
| :--- | :--- | :--- |
| `llm-shield-proxy` | 1.6.x | Yes |
| `llm-shield-proxy` | < 1.6 | No |
| `pii-leak-benchmark` | 0.1.x | Yes |

Only the latest patch release of a supported line receives fixes. There are no long-term
support branches.

## Scope

This repository ships two things, and a report should say which one it is about.

**`llm-shield-proxy`** is a privacy gateway that sits in a request path. In scope: bypasses
of the redaction pipeline, SSRF and DNS-rebinding defeats in the egress guard, RBAC and
policy bypass, audit-chain forgery or sequence manipulation, PII reaching logs, traces or
exception paths, and any fail-open behaviour in a control documented as fail-closed.

**`pii-leak-benchmark`** is a measuring instrument. In scope, and treated as seriously as a
proxy bug: **anything that makes the harness report a target as safer than it is.** Every
inspector defect found in this project so far has flattered the target, so a false pass is
the failure mode that matters most here. Missed leak detection, capture-server attribution
errors, and decoding or normalisation gaps all count.

### Out of scope

- Findings against a deployment you do not operate or have permission to test.
- Missing detections that are a stated limitation. The proxy makes no completeness claim
  for detection on unlabelled traffic; see [`LIMITATIONS.md`](LIMITATIONS.md).
- Absence of a control the documentation already says is absent. Tamper-evident audit
  logging is not WORM without
  [immutable retention](website/docs/immutable-retention.md), and this is documented, not
  a defect.
- Benchmark results you consider unfair or wrong. Those are a public discussion, not a
  vulnerability - open an issue.

## Related

This file is the vulnerability *disclosure policy*. For the threat model, the OWASP LLM
mapping, and what each control does, see
[`website/docs/security.md`](website/docs/security.md), which is a different document with
a different purpose.
