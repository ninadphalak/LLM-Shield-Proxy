# Role-Based Policy Configuration

`policies.yaml` defines roles and maps request identifiers, such as `virtual_key_id` or
`x-tenant-id`, to those roles.

Instead of applying the same global security rules to everyone, you can give a trusted internal developer team a "relaxed" security profile (for faster response times), while enforcing strict data protection rules for an external customer-facing application.

By default, the proxy operates under a strict **Zero-Trust (`FAIL_CLOSED`)** model. If a client attempts to connect with an unknown identifier that is not mapped to a role (and no `default_role` is defined), the connection is immediately blocked with an `HTTP 403 Forbidden` error.

## Supported request-scoped controls

The dynamic settings path can apply supported role-specific values. It should not be treated as permission to override every `.env` field: process-start configuration, keys, transports, resource pools, and security boundaries require explicit code and tests.

While you can override network limits (e.g., `RATE_LIMIT_RPM`, `MAX_PAYLOAD_SIZE_BYTES`) or masking modes (`SHIELD_DEFAULT_MASKING_MODE`), the core security features commonly overridden are:

1. **`ENABLE_CANARY_TRIPWIRE`** (bool):
   - **What it does:** Adds a unique marker to the system prompt before sending it upstream.
   - **How it helps:** If the inspected output contains the marker, the proxy records a correlation signal. Marker matches do not prove intent or attribution and can be evaded or triggered accidentally.
   - **When to use:** Enable this for any external, untrusted, or customer-facing applications where prompt security is a concern.
   - [Learn more about Prompt Correlation Markers](/docs/features/advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires)

2. **`ENABLE_BLAST_RADIUS_LIMITS`** (bool):
   - **What it does:** Monitors the number of sensitive entities (like credit cards or SSNs) detected in a single session.
   - **How it helps:** When the configured entity threshold is crossed, the proxy blocks the applicable request or stream path. It limits that observed path; it does not rule out missed entities or other egress paths.
   - **When to use:** Enable this for high-risk applications handling bulk financial or healthcare data.
   - [Learn more about Entity-Weighted Request Limits](/docs/features/advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits)

3. **`ENABLE_FINOPS_METERING`** (bool):
   - **What it does:** Extracts token usage statistics from the LLM provider's response stream.
   - **How it helps:** Attributes reported token usage to the configured tenant or role. Reconcile provider invoices, retries, cached tokens, and missing usage events before using it for chargeback.
   - **When to use:** Enable this when you need to track budgets across different teams or bill clients for their AI usage.
   - [Learn more about Token Usage Cost Estimates](/docs/features/advanced-threat-defense-enterprise-resilience/llm-finops-chargeback-meter)

4. **`ENABLE_TIER3_ONNX_NER`** (bool):
   - **What it does:** Runs the configured ONNX named-entity recognition model in addition to
     pattern checks.
   - **How it helps:** Can find contextual entities that regular expressions miss. Accuracy and
     latency depend on the selected model and workload.
   - **When to use:** Enable it when the risk analysis requires contextual detection, including workloads subject to HIPAA safeguards. The selected model still needs corpus-specific validation and does not establish compliance.



> [!NOTE]
> Local role lookup uses an in-memory dictionary. A background poll checks `policies.yaml` at `POLICIES_RELOAD_INTERVAL_SECONDS` (default 5s); observed reload time includes polling, file propagation, parsing, and scheduling. Test malformed files and in-flight request behavior.

---

## Example `policies.yaml` Template

This starting template defines three example roles. Review every value before using it in a
deployment.

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
    # Add a correlation marker to the prompt
    ENABLE_CANARY_TRIPWIRE: true
    # Stop the supported path after the configured entity threshold
    ENABLE_BLAST_RADIUS_LIMITS: true
    # Record provider-reported token usage for cost estimates
    ENABLE_FINOPS_METERING: true
    # Run the configured ONNX named-entity model
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

* **Unknown-key behavior:** Omit `default_role` when unknown or unmapped virtual keys should be denied. Verify this behavior on every router and resolver. The MCP gateway separately defaults empty allowlists to `DENY_ALL`; setting `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY` explicitly permits every tool not listed in `blocked_tools` and emits a critical startup warning.
* **Latency Optimization:** For internal tools or trusted developer sandboxes, disable `ENABLE_TIER3_ONNX_NER` to bypass neural inference. Measure end-to-end overhead in your deployment.
* **Canary tripwire:** Consider `ENABLE_CANARY_TRIPWIRE` only after testing false positives, marker survival, output handling, and privacy impact. A marker match is a correlation signal, not definitive attribution.

## Frequently Asked Questions (FAQ)

**Q: Do I need to restart the proxy when I update `policies.yaml`?**
For the local file resolver, no process restart is intended. The file is checked every 5 seconds by default (configurable via `POLICIES_RELOAD_INTERVAL_SECONDS`), so changes are not instantaneous and malformed updates can be rejected.

**Q: What happens to active, streaming LLM requests when the policy changes?**
Requests use the policy resolved at the documented boundary. Test in-flight behavior, cache invalidation, and reload timing for the selected resolver; a process or dependency failure can still interrupt streams.

**Q: What if I have thousands of virtual keys? Will it slow down the proxy?**
The local mapping uses a dictionary lookup, but complete policy-resolution latency depends on cache state, resolver type, concurrency, and external services. Measure it under the intended deployment profile.

**Q: What if I accidentally make a typo or syntax error in my `policies.yaml` file?**
For handled parse failures, the reload path records a warning and retains the last valid in-memory policy. Test startup without a valid policy, partial writes, resolver errors, and process restarts separately.
