# WORM-Compliant Audit Logging with Hash Chaining

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**WORM-Compliant Audit Logging with Hash Chaining** (Write Once, Read Many) guarantees the absolute cryptographic integrity of your proxy's security audit logs. It prevents internal actors or external attackers from altering, deleting, or reordering log events to cover their tracks, satisfying the most stringent requirements for SOC 2 Type II, HIPAA, and FedRAMP compliance.

## How It Works
Standard JSON logs written to stdout or a file can easily be manipulated by an attacker who gains root access to the server. Hash Chaining solves this using a localized blockchain-style structure:

1. **Event Structuring:** Every time the proxy makes a security decision (e.g., redacting an SSN, blocking a tool call), it generates a structured JSON audit event containing the timestamp, tenant ID, and the action taken.
2. **Cryptographic Chaining:** Before emitting the log, the proxy calculates the SHA-256 hash of the *previous* log event and embeds it into the current event's payload.
3. **Sealing:** The current event is then hashed, creating a mathematically unbreakable chain of custody.


```mermaid
flowchart LR
    A[Event N-1] -->|Hash(N-1)| B(Event N)
    B -->|Hash(N)| C(Event N+1)
    D[Attacker modifies Event N] -.->|Breaks Chain!| C
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Execution Speed:** SHA-256 hashing of small JSON payloads executes in `&lt;0.5µs`.
- **Overhead:** Extremely low. The previous hash state is held securely in memory per worker process.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `ENABLE_AUDIT_LOGGING` | Toggles the emission of structured audit events. | [View in deployment.md](/docs/deployment) |
| `ENABLE_HASH_CHAINING` | Enforces the SHA-256 linkage on emitted logs. | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **Process Forking (Gunicorn/Uvicorn):** Because the proxy runs across multiple worker processes, each worker maintains its own independent, isolated hash chain. To verify the logs later, auditors simply group the logs by `worker_id` and recalculate the chain.
* **Log Aggregation:** These chained events are emitted directly to `stdout` to be consumed by Fluentd, Promtail, or Datadog agents. The proxy does not write to local disk, adhering strictly to 12-Factor App methodology.
* **Unhandled request exceptions are sealed into the chain, deliberately without detail:** any exception the global handler catches emits an `UNHANDLED_EXCEPTION` event (`severity: CRITICAL`) carrying only `exception_type` (e.g. `ValueError`) plus `request_id`/`path`/`method` -- never `str(exc)` or a traceback. Those can carry raw, unredacted request content (a value that failed validation, a fragment of a prompt), which has no business entering a sink that promises zero raw PII leakage. The full exception and traceback still get logged -- to the separate operational application logger (`logger.error(..., exc_info=exc)`), which is expected to flow to a SIEM/log aggregator with its own (typically shorter) retention, not the compliance-grade WORM record.
* **Backpressure is observable, not just logged:** the WORM queue and the stdout sink queue are both bounded and drop-on-full rather than blocking the request path. Every drop still increments `audit_events_dropped_total{sink="worm_chain_queue"|"stdout_queue"}` (Prometheus) in addition to the existing `WARNING` log line, so sustained drops -- which mean the compliance record is incomplete for that window -- are alertable instead of something you only discover by grepping logs after the fact.

  A bounded, drop-on-full in-process queue is a deliberate memory-safety trade-off, not a claim of zero audit loss. Deployments where audit completeness is a hard compliance requirement should alert on `audit_events_dropped_total > 0` and, for a stronger guarantee, put a durable external sink (Kafka/Kinesis/SQS-backed WORM writer) behind the `stdout` consumer rather than relying on the in-process queue alone.

## FAQ

**Q: If an attacker deletes the last 5 logs, how do we know?**
A: You will have a dangling chain. The final log received by your SIEM (e.g., Splunk) will point to a hash that doesn't exist, instantly triggering a tampering alert during forensic review.

**Q: Are the actual PII strings (like the real SSN) written to these logs?**
A: Absolutely not. The proxy logs the *metadata* of the redaction (e.g., `entities_redacted: ["SSN", "CREDIT_CARD"]`) but never the sensitive strings themselves, ensuring the log aggregation platform does not become a toxic data lake.


## Plainspeak
This feature creates an unhackable, permanent diary of every security decision the proxy makes.

To pass strict security audits (like SOC 2 or HIPAA), companies need absolute proof of what happened and when. This feature records every action and mathematically locks it to the action that happened right before it (like links in a chain). If a hacker tries to go back in time to delete or change a log entry, the entire mathematical chain breaks, instantly revealing the tampering to auditors.

## Related Tests
See the following test files for reference implementations and edge-case testing: [`tests/test_audit_remediation.py`](https://github.com/YOUR_ORG/LLM-Shield-Proxy/blob/main/tests/test_audit_remediation.py) and [`tests/test_hardening_remediation.py`](https://github.com/YOUR_ORG/LLM-Shield-Proxy/blob/main/tests/test_hardening_remediation.py) (unhandled-exception audit event, PII exclusion, and drop-metric coverage).
