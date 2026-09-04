# Role-Based Policy Configuration

The `policies.yaml` file defines security roles and maps incoming request identifiers (e.g., `virtual_key_id` or `x-tenant-id`) to those roles. 

This allows you to apply different security profiles-such as strict data protection for external users, and relaxed boundaries for internal developers-within the same proxy instance.

By default, the proxy operates under a strict **Zero-Trust (`FAIL_CLOSED`)** model. Requests using an unknown identifier that is not mapped to a role (and lacks a `default_role`) are rejected with `HTTP 403 Forbidden`.

## Configurable Request-Scoped Controls

The following core security features can be overridden on a per-role basis:

1. **`ENABLE_CANARY_TRIPWIRE`** (boolean)
   - Injects a unique cryptographic marker into the system prompt. If this marker appears in the LLM output, it provides a heuristic signal for prompt extraction.

2. **`ENABLE_BLAST_RADIUS_LIMITS`** (boolean)
   - Monitors the total count of sensitive entities (e.g., SSNs, credit cards) detected in a single session. Reaching the threshold severs the connection.

3. **`ENABLE_FINOPS_METERING`** (boolean)
   - Extracts token usage statistics from the LLM provider's response and attributes them to the assigned role/tenant for cost tracking.

4. **`ENABLE_TIER3_ONNX_NER`** (boolean)
   - Enables the local ONNX named-entity recognition model for contextual PII detection. 

5. **`payload_skip_keys`** (list of strings)
   - JSON keys this role's traffic carries that redaction must not walk or rewrite.
   - Redaction walks every field a request carries, because the proxy forwards every
     field. JSON is schemaless, so a deployment's own field names cannot be known in
     advance. Naming them here is how you declare them.
   - Use it for two cases: a value that must reach the provider byte for byte, and a
     large binary field that costs time to walk and can never contain matchable text.
   - Sibling fields are still redacted. Only the named keys are skipped.

*Note: You can also override network limits like `RATE_LIMIT_RPM` and `MAX_PAYLOAD_SIZE_BYTES` per role.*

## Example `policies.yaml` Template

```yaml
# 1. Role Definitions
roles:
  strict_compliance_role:
    ENABLE_CANARY_TRIPWIRE: true
    ENABLE_BLAST_RADIUS_LIMITS: true
    ENABLE_FINOPS_METERING: true
    ENABLE_TIER3_ONNX_NER: true
    MAX_PAYLOAD_SIZE_BYTES: 204800
    RATE_LIMIT_RPM: 60
    # This tenant posts base64 frames in a field of their own. Skipping it keeps the
    # walk off a 1 MB string that no text detector could match anyway.
    payload_skip_keys:
      - internal_vision_blob

  developer_sandbox:
    ENABLE_CANARY_TRIPWIRE: false
    ENABLE_BLAST_RADIUS_LIMITS: false
    ENABLE_FINOPS_METERING: true
    ENABLE_TIER3_ONNX_NER: false
    # Signed request bodies break if a single byte changes.
    payload_skip_keys:
      - webhook_signature
      - raw_document

# 2. Virtual Key Mapping
virtual_keys:
  "vk-prod-finance-001": "strict_compliance_role"
  "vk-dev-sandbox-001": "developer_sandbox"

# 3. Default Fallback Role
# Uncomment to apply a role to unmapped keys instead of blocking them.
# default_role: "strict_compliance_role"
```

## Reload Behavior

The proxy polls the local `policies.yaml` file for changes every 5 seconds (configurable via `POLICIES_RELOAD_INTERVAL_SECONDS`). Process restarts are not required. If a syntax error is introduced to the file, the proxy logs a warning and retains the last known good configuration in memory.
