# SOC 2 control support and evidence

A SOC 2 examination evaluates an organization's controls and, for Type II, their operation over
a period of time. LLM-Shield-Proxy can provide technical controls and evidence for an auditor to
evaluate. It does not make a service SOC 2 compliant.

## Logical access (CC6.1)

The proxy can map a virtual key to a configured role and apply that role on supported request
paths. OPA, Vault, and local policy resolvers have different cache and failure behavior. Operators
must test unknown identities, resolver outages, stale policy, revocation, and routing bypasses.

The `/v1/mcp` route applies policy to a limited JSON-RPC method set. It is not a complete MCP
transport. Empty allowlists deny all tools by default; blocklist-only behavior requires an
explicit setting and permits every tool not listed.

## Boundary protection (CC6.6)

The proxy can transform detected values before the configured upstream request is built. The
HTTP conformance profile checks its own declared fixtures at that boundary. A pass is not a claim
about all data, detector recall, other destinations, or network isolation.

Supported outbound URL paths can also apply SSRF and DNS-rebinding checks. The protection depends
on routing every relevant request through those paths and on the configured allow and deny rules.

## Monitoring and evidence (CC7.2)

- Audit records can use SHA-256 predecessor links and Ed25519 signatures. Verification detects
  changes within the supplied chain but does not prove complete capture or immutable storage.
- OpenTelemetry and Prometheus provide operational signals. Sampling, queue pressure, exporter
  failures, retention, and alert delivery can create gaps.
- The canary, watermark, and agent-loop features are heuristic research controls. A marker is a
  correlation signal, not proof of who disclosed content. A repeated-call threshold is not a
  general detector for unsafe agent behavior.

Use [durable audit delivery and external retention](/docs/immutable-retention) where the evidence
requirements call for them. See the [compliance evidence boundaries](/docs/compliance-overview)
before mapping these features to controls.
