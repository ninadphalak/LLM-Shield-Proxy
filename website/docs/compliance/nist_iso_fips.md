# NIST, ISO/IEC 42001, and FIPS evidence support

LLM-Shield-Proxy can produce selected evidence for NIST SP 800-53 and ISO/IEC 42001 control
assessments. It also runs narrow cryptographic known-answer tests. These features do not certify
the application, cryptographic module, deployment, or organization.

## OSCAL assessment output

The offline assessment and decision-trace components can create OSCAL 1.2
`assessment-results` artifacts. OSCAL is a machine-readable exchange format. The output records
selected observations; it does not prove that a control is effective or complete.

The default assessment-plan reference is a placeholder. Replace it with the plan used by the
deployment before treating the artifact as formal evidence. GRC delivery also requires explicit
application wiring or a separate connector; the proxy does not automatically send these records
to Vanta, Drata, or another GRC service.

## Audit evidence

Audit records can use SHA-256 predecessor links and Ed25519 signatures. With a separately trusted
public key, the verifier checks continuity and authenticity within the supplied chain. Local
storage is not WORM, the default delivery mode can drop events, and an external anchor is needed
to detect deletion from the end of a chain.

RFC 6902 output can describe selected mutation operations without storing the original matched
value. Verify errors, custom fields, traces, and downstream log systems because the format alone
does not guarantee data minimization.

## FIPS 140-3 boundary

At startup, the application runs fixed SHA-256 and AES-256-GCM known-answer tests. With
`FIPS_STRICT_MODE=true`, a failed test stops startup. A pass shows only that those test vectors
produced the expected results in that run.

The tests do not make Python, OpenSSL, the host, or the application a FIPS 140-3 validated
cryptographic module. A FIPS claim requires a validated module operated within its security
policy and an assessment of the complete deployment boundary.

See the [compliance evidence boundaries](/docs/compliance-overview) and
[FIPS and differential audit feature page](/docs/features/enterprise-auditing-compliance/fips-140-3-kat-rfc-6902-differential-audit-logging).
