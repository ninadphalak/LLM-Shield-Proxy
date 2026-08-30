# 🛡️ Role-Based Policy-as-Code (RBAC)

LLM-Shield-Proxy provides a system called "Policy-as-Code" via a file named `policies.yaml`. This file allows you to define custom security rules (roles) and assign them to specific users, clients, or departments (identified by a `virtual_key_id` or `x-tenant-id`).

Instead of applying the same global security rules to everyone, you can give a trusted internal developer team a "relaxed" security profile (for faster response times), while enforcing strict data protection rules for an external customer-facing application.

By default, the proxy operates under a strict **Zero-Trust (`FAIL_CLOSED`)** model. If a client attempts to connect with an unknown identifier that is not mapped to a role (and no `default_role` is defined), the connection is immediately blocked with an `HTTP 403 Forbidden` error.

## Supported Security Controls (Universal Override)

With the introduction of the **Universal Dynamic Override Engine**, you can dynamically turn *any* configuration property defined in your global `.env` file on or off for any specific role. This radically reduces global configuration burden.

While you can override network limits (e.g., `RATE_LIMIT_RPM`, `MAX_PAYLOAD_SIZE_BYTES`) or masking modes (`SHIELD_DEFAULT_MASKING_MODE`), the core security features commonly overridden are:

1. **`ENABLE_CANARY_TRIPWIRE`** (bool):
   - **What it does:** Silently injects a unique, tracking "honeytoken" string into the system prompt before sending it to the LLM.
   - **How it helps:** If the LLM ever outputs this hidden string, you have absolute proof that a user attempted to extract the system prompt (a prompt-leak attack).
   - **When to use:** Enable this for any external, untrusted, or customer-facing applications where prompt security is a concern.
   - [Learn more about Canary Tripwires](/docs/features/advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires)

2. **`ENABLE_BLAST_RADIUS_LIMITS`** (bool):
   - **What it does:** Monitors the number of sensitive entities (like credit cards or SSNs) detected in a single session.
   - **How it helps:** If a user tries to suddenly copy-paste a massive database of sensitive information into an LLM, this limit triggers and instantly cuts off the connection, preventing mass data exfiltration.
   - **When to use:** Enable this for high-risk applications handling bulk financial or healthcare data.
   - [Learn more about Blast Radius Limits](/docs/features/advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits)

3. **`ENABLE_FINOPS_METERING`** (bool):
   - **What it does:** Extracts token usage statistics from the LLM provider's response stream.
   - **How it helps:** Allows you to calculate exactly how much money each individual department or client is costing you in AI API usage (Chargeback metering).
   - **When to use:** Enable this when you need to track budgets across different teams or bill clients for their AI usage.
   - [Learn more about FinOps Metering](/docs/features/advanced-threat-defense-enterprise-resilience/llm-finops-chargeback-meter)

4. **`ENABLE_TIER3_ONNX_NER`** (bool):
   - **What it does:** Activates a Deep Learning AI model (Named Entity Recognition) to read the text and find complex sensitive data based on context, not just simple patterns.
   - **How it helps:** Finds hidden PII (Personally Identifiable Information) that standard regular expressions miss. However, it takes slightly longer to run.
   - **When to use:** Enable it when the risk analysis requires contextual detection, including workloads subject to HIPAA safeguards. The selected model still needs corpus-specific validation and does not establish compliance.



> [!NOTE]
> All policy changes are flattened into an O(1) dictionary in-memory (meaning lookups are instant regardless of size). The engine checks the `policies.yaml` file on a background thread (configured by `POLICIES_RELOAD_INTERVAL_SECONDS`, default 5s). When you save the file, the proxy updates immediately without dropping any active user connections.

---

## Example `policies.yaml` Template

Below is a production-ready template that defines three distinct roles. Copy and paste this into your `policies.yaml` file.

```yaml
# =========================================================
# LLM-Shield-Proxy Hierarchical Policy-as-Code
# =========================================================

# 1. Role Definitions
# Define your reusable security profiles here. Each role groups settings together.
roles:
  # ---------------------------------------------------------
  # Strict Compliance Role (For External / Customer-Facing Apps)
  # ---------------------------------------------------------
  strict_compliance_role:
    # Inject tracking tokens to catch prompt hackers
    ENABLE_CANARY_TRIPWIRE: true
    # Halt the stream if a user tries to upload massive amounts of PII
    ENABLE_BLAST_RADIUS_LIMITS: true
    # Track exactly how many tokens this app consumes for billing
    ENABLE_FINOPS_METERING: true
    # Use the heavy AI model for maximum data privacy scanning
    ENABLE_TIER3_ONNX_NER: true
    # Universal Overrides: Restrict payload size for this tenant
    MAX_PAYLOAD_SIZE_BYTES: 204800
    RATE_LIMIT_RPM: 60

  # ---------------------------------------------------------
  # Developer Sandbox Role (For Internal Engineering Teams)
  # ---------------------------------------------------------
  developer_sandbox:
    # Engineers don't need prompt-leak tracking
    ENABLE_CANARY_TRIPWIRE: false
    # Engineers might paste large logs, don't cut them off
    ENABLE_BLAST_RADIUS_LIMITS: false
    # We still want to track how much money the engineers are spending
    ENABLE_FINOPS_METERING: true
    # Disable the optional neural model to reduce work on the request path
    ENABLE_TIER3_ONNX_NER: false

  # ---------------------------------------------------------
  # Internal Services (For automated background bots)
  # ---------------------------------------------------------
  internal_services:
    ENABLE_CANARY_TRIPWIRE: true
    ENABLE_BLAST_RADIUS_LIMITS: false
    ENABLE_FINOPS_METERING: false
    ENABLE_TIER3_ONNX_NER: true

# 2. Virtual Key to Role Mapping
# Assign specific API keys, tenant IDs, or users to the roles defined above.
virtual_keys:
  # Map two different production apps to the strict security role
  "vk-prod-finance-001": "strict_compliance_role"
  "vk-prod-healthcare-002": "strict_compliance_role"

  # Map the internal engineering team to the fast, relaxed role
  "vk-dev-sandbox-001": "developer_sandbox"

  # Map an internal HR bot to the internal services role
  "vk-internal-hr-bot": "internal_services"

# 3. Default Fallback Role (Optional)
# If someone connects with a virtual key that is NOT listed above,
# you can either block them completely (by commenting this out),
# or assign them a default role.
# default_role: "strict_compliance_role"
```

---

## Enterprise Recommendations & Best Practices

* **Zero-Trust Architecture:** Omit the `default_role` key in production. This guarantees that unknown or unmapped virtual keys are strictly denied access (`FAIL_CLOSED`), ensuring only explicitly authorized tenants can route through the proxy.
* **Latency Optimization:** For internal tools or trusted developer sandboxes, disable `ENABLE_TIER3_ONNX_NER` to bypass neural inference. Measure end-to-end overhead in your deployment.
* **Forensics & Insider Threats:** Always enable `ENABLE_CANARY_TRIPWIRE` for third-party or untrusted downstream applications. This silently injects a cryptographic honeytoken that, if leaked or repeated by the LLM, allows you to definitively trace the prompt extraction attempt.

## Frequently Asked Questions (FAQ)

**Q: Do I need to restart the proxy when I update `policies.yaml`?**
No. The proxy updates automatically. The file is checked every 5 seconds (configurable via `POLICIES_RELOAD_INTERVAL_SECONDS`). Just save the file, and the proxy applies the new rules instantly.

**Q: What happens to active, streaming LLM requests when the policy changes?**
Active requests keep the exact rules they had when the connection started. Only new requests receive the updated rules. Active user streams are never interrupted.

**Q: What if I have thousands of virtual keys? Will it slow down the proxy?**
The local mapping uses a dictionary lookup, but complete policy-resolution latency depends on cache state, resolver type, concurrency, and external services. Measure it under the intended deployment profile.

**Q: What if I accidentally make a typo or syntax error in my `policies.yaml` file?**
The proxy will log a warning that the YAML file is broken, but it will safely ignore the broken file. It will continue serving traffic using the last-known valid policies in memory. It will never crash or accidentally disable security.
