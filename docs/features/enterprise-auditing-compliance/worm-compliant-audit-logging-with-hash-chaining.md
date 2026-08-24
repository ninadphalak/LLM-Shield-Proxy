# WORM-Compliant Audit Logging with Hash Chaining

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
**WORM-Compliant Audit Logging with Hash Chaining** (Write Once, Read Many) guarantees the absolute cryptographic integrity of your proxy's security audit logs. It prevents internal actors or external attackers from altering, deleting, or reordering log events to cover their tracks, satisfying the most stringent requirements for SOC 2 Type II, HIPAA, and FedRAMP compliance.

## How It Works
Standard JSON logs written to stdout or a file can easily be manipulated by an attacker who gains root access to the server. Hash Chaining solves this using a localized blockchain-style structure:

1. **Event Structuring:** Every time the proxy makes a security decision (e.g., redacting an SSN, blocking a tool call), it generates a structured JSON audit event containing the timestamp, tenant ID, and the action taken.
2. **Cryptographic Chaining:** Before emitting the log, the proxy calculates the SHA-256 hash of the *previous* log event and embeds it into the current event's payload. 
3. **Sealing:** The current event is then hashed, creating a mathematically unbreakable chain of custody.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart LR
    A[Event N-1] -->|Hash(N-1)| B(Event N)
    B -->|Hash(N)| C(Event N+1)
    D[Attacker modifies Event N] -.->|Breaks Chain!| C
```
-->

View diagram on GitHub mobile 📱 -->
![Hash Chaining Architecture](../images/worm-compliant-audit-logging-with-hash-chaining.svg)

## Performance Profile
- **Execution Speed:** SHA-256 hashing of small JSON payloads executes in `<0.5µs`.
- **Overhead:** Extremely low. The previous hash state is held securely in memory per worker process.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_AUDIT_LOGGING` | Toggles the emission of structured audit events. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |
| `ENABLE_HASH_CHAINING` | Enforces the SHA-256 linkage on emitted logs. | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Process Forking (Gunicorn/Uvicorn):** Because the proxy runs across multiple worker processes, each worker maintains its own independent, isolated hash chain. To verify the logs later, auditors simply group the logs by `worker_id` and recalculate the chain.
* **Log Aggregation:** These chained events are emitted directly to `stdout` to be consumed by Fluentd, Promtail, or Datadog agents. The proxy does not write to local disk, adhering strictly to 12-Factor App methodology.

## FAQ

**Q: If an attacker deletes the last 5 logs, how do we know?**
A: You will have a dangling chain. The final log received by your SIEM (e.g., Splunk) will point to a hash that doesn't exist, instantly triggering a tampering alert during forensic review.

**Q: Are the actual PII strings (like the real SSN) written to these logs?**
A: Absolutely not. The proxy logs the *metadata* of the redaction (e.g., `entities_redacted: ["SSN", "CREDIT_CARD"]`) but never the sensitive strings themselves, ensuring the log aggregation platform does not become a toxic data lake.


## Plainspeak
This feature creates an unhackable, permanent diary of every security decision the proxy makes.

To pass strict security audits (like SOC 2 or HIPAA), companies need absolute proof of what happened and when. This feature records every action and mathematically locks it to the action that happened right before it (like links in a chain). If a hacker tries to go back in time to delete or change a log entry, the entire mathematical chain breaks, instantly revealing the tampering to auditors.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_audit_remediation.py`](../../../tests/test_audit_remediation.py).
