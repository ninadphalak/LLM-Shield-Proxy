# EU AI Act engineering support

Whether the EU AI Act applies, how a system is classified, and which provider or deployer duties
apply require a separate legal and system analysis. LLM-Shield-Proxy can support parts of a
record-keeping and oversight design. It does not establish conformity.

## Article 12 record-keeping support

The proxy can emit audit metadata for instrumented events. Records may include sequence numbers,
SHA-256 predecessor links, and Ed25519 signatures. The verifier can detect changes and gaps within
the chain it receives.

This is not automatically a complete record of system operation:

- the default `best_effort` mode can drop events when its bounded queue is full;
- local JSONL files remain deletable by an administrator;
- multiple workers have separate chains rather than one global order; and
- deletion of an unanchored suffix cannot be detected from the shortened file alone.

Use acknowledged audit delivery, independently managed retention, and external terminal-state
anchors when the assessment requires those properties. OSCAL output can package selected
observations for exchange; it does not prove control effectiveness.

## Article 14 human-oversight support

Configured policy checks can deny supported tool calls, and the agent-loop breaker can return
HTTP 429 after a repeated-action threshold. These controls can enforce specific operator-defined
limits. They do not supply human review, explain model behavior, cover every tool route, or detect
every unsafe loop.

Document who sets policy, who reviews denials and alerts, how an operator can intervene, and what
happens when a policy service is unavailable. See [MCP tool governance](/docs/guides/mcp-tool-governance)
and the [compliance evidence boundaries](/docs/compliance-overview).
