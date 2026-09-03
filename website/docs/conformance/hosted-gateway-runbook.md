---
sidebar_position: 7
---

# Runbook: Measuring a Hosted Gateway

This runbook covers Cloudflare AI Gateway and Portkey, the two hosted targets evaluated in the comparison table.

Items that do not require a vendor account have been executed and verified. Items that require a vendor account are marked **(NOT RUN)** and are based on vendor documentation, cited inline. A row in [the results table](./results) should remain marked as `Not run` until the pinned configuration and JSON report are produced and verified.

## Understanding Output Claims

The harness derives the `outcome` field from what the product **claims** about PII redaction, how it is **configured**, and the final **measured result**. You supply the initial claims, and the harness strictly computes the outcome. Manually editing the outcome field will fail schema validation.

| `outcome` | Meaning | Published Status |
| :--- | :--- | :--- |
| `pass` | Redaction is claimed, enabled, and all checks passed. | Pass |
| `fail` | **The gateway leaked an unmasked test value to the capture server.** (This is the only leak finding). | Fail, with `leak_evidence` |
| `no-leak-profile-not-met` | No test value leaked, but another check failed (e.g., one-way anonymization without restoration). | "No leak; original-value restoration not provided." (Not a privacy failure). |
| `not-applicable` | The product does not claim PII redaction capabilities. | "Not applicable - no redaction feature offered." |
| `redaction-not-enabled` | Redaction is offered but was not enabled in the test. | Configuration issue (Enable it and re-run). |
| `inconclusive` | The target did not reach the capture server (no correlation). | Invalid run (Fix configuration and re-run). |
| `claim-unstated` | The claim was not recorded. | Invalid run. |

**Important Context for Specific Gateways:**
- **Cloudflare AI Gateway:** Does not claim PII redaction. Its DLP feature can *flag* (returning original response) or *block* (returning a 400 error). Neither action redacts. A "Fail" result would be inaccurate since it does not offer redaction.
- **Portkey:** Redacts but does not rehydrate. PII Redaction replaces values with identifiers (e.g., `{{EMAIL_ADDRESS_1}}`) but provides no restoration. This leads to a `response_fidelity` failure while leaking nothing (`no-leak-profile-not-met`).

## Step 1: Setting up a Public Capture

Hosted gateways cannot reach your local loopback. You must expose the capture endpoint via a public tunnel.

Bind the capture to **loopback** and use a tunnel to route external traffic to it. This ensures the port itself is not directly exposed to the internet.

```bash
# 1. Install the standalone harness. (Does not install a gateway).
python -m pip install pii-leak-benchmark
python -m pip install jsonschema          # Optional: validates the artifact

# 2. Start a tunnel (e.g., Cloudflare quick tunnels require no account).
cloudflared tunnel --url http://localhost:8765
# Output will provide a URL, e.g., https://your-quick-tunnel.trycloudflare.com
```

```bash
# 3. Export a secure capture token.
export CONFORMANCE_CAPTURE_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
echo "$CONFORMANCE_CAPTURE_TOKEN"   
```
*Note: Provide this token to the vendor as the upstream API key. Always use environment variables rather than CLI flags to prevent exposure in process listings.*

### Tunnel Traffic Inspection

The tunnel must reliably pass traffic for accurate measurement:

| Property | Result |
| :--- | :--- |
| HTTP version | HTTP/1.1 (Cloudflared speaks HTTP/1.1 to origin). |
| Transfer framing | `content-length` is preserved; no chunked re-framing occurs. |
| Body | Byte-identical to the direct request. |
| `Authorization` | **Survives intact** (required for token attribution). |
| Headers added | Cloudflare specific headers (e.g., `cf-ray`, `x-forwarded-for`). |
| Headers removed | None. |

Because the capture server inspects headers, newly added headers like IP addresses (e.g., `x-forwarded-for`) can sometimes match digit-based PII (like an SSN). Always check `leak_evidence` to see if a match is `literal` or `normalized`.

## Step 2a: Cloudflare AI Gateway (NOT RUN - Requires Account)

Cloudflare AI Gateway is available on all plans. You need a Cloudflare account and an API token with `AI Gateway - Edit` permissions.

Configure a [Custom Provider](https://developers.cloudflare.com/ai-gateway/configuration/custom-providers/) pointing to your tunnel URL.

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/ai-gateway/custom-providers" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Conformance Capture",
    "slug": "conformance-capture",
    "base_url": "https://your-quick-tunnel.trycloudflare.com",
    "enable": true
  }'
```

Execute the benchmark using the capture token as the provider credential:

```bash
pii-leak-benchmark \
  --target-base-url "https://gateway.ai.cloudflare.com/v1/$CF_ACCOUNT_ID/$CF_GATEWAY/conformance-capture" \
  --target-api-key "$CONFORMANCE_CAPTURE_TOKEN" \
  --target-name "cloudflare-ai-gateway" \
  --target-version "<observed dashboard build/date>" \
  --iterations 3 \
  --capture-port 8765 \
  --capture-public-url "https://your-quick-tunnel.trycloudflare.com/v1" \
  --redaction-claimed not-offered \
  --redaction-claim-citation "https://developers.cloudflare.com/ai-gateway/features/dlp/ (Actions: Flag/Block. Neither redacts.)" \
  --json-out cloudflare-ai-gateway.json
```

**Expect `outcome: not-applicable`**: The fixture will reach your capture, and all entities will be listed in `leaked_entity_types`. This accurately measures a product that does not offer redaction. Publish as: "Not applicable - detects and blocks, does not redact".

## Step 2b: Portkey (NOT RUN - Requires Account)

**Account Tier:** [Guardrails are available on all plans](https://docs.portkey.ai/docs/product/guardrails), though features vary:
- **Developer (Free):** Uses `Regex Match` (supports Redact toggle).
- **Production:** Includes `Portkey Pro PII` detector.

When testing, specify which detector was used:
- **Portkey Pro PII**: Measures Portkey's actual detection quality.
- **Regex Match**: Measures Portkey's gateway engine using tester-authored patterns (state this in `--redaction-config-reference`).

**Enabling PII Redaction:**
1. Go to **Guardrails → Create**.
2. Select a PII guardrail and enable **Redact PII**, or select **Regex Match** and toggle **Redact** ON for patterns like SSN, Card, and Email.
3. Save and copy the **Guardrail ID**.
4. Attach it to a config as a `before_request_hooks` entry.

You can configure the custom host directly via headers; the dashboard is not required. *Note: Portkey blocks private/reserved IPs by default, so a public tunnel hostname is mandatory.*

```bash
pii-leak-benchmark \
  --target-base-url "https://api.portkey.ai/v1" \
  --target-api-key "$PORTKEY_API_KEY" \
  --target-header "x-portkey-provider=openai" \
  --target-header "x-portkey-custom-host=https://your-quick-tunnel.trycloudflare.com/v1" \
  --target-header "x-portkey-config=$PORTKEY_CONFIG_ID" \
  --target-header "Authorization=Bearer $CONFORMANCE_CAPTURE_TOKEN" \
  --target-header "x-portkey-forward-headers=Authorization" \
  --target-name "portkey" \
  --target-version "<observed>" \
  --iterations 3 \
  --capture-port 8765 \
  --capture-public-url "https://your-quick-tunnel.trycloudflare.com/v1" \
  --redaction-claimed claimed \
  --redaction-claim-citation "https://docs.portkey.ai/docs/product/guardrails/pii-redaction" \
  --redaction-enabled \
  --redaction-config-reference "<Guardrail ID>, Redact PII ON" \
  --json-out portkey.json
```

**Expect `outcome: no-leak-profile-not-met`**: Portkey redacts values but does not rehydrate them. `configured_upstream_boundary` should pass with an empty `leaked_entity_types`, while `response_fidelity` and `fragmentation_safety` fail.

## Step 3: Run Validation

Before publishing, verify the artifact:

1. `outcome`: Must be `pass`, `fail`, or `no-leak-profile-not-met`.
2. `capture.self_probe.advertised_url_reachable`: Must be `true`. If false, the target may not have reached the capture server.
3. `correlated_requests`: Should equal your `--iterations`.
4. `unattributed_requests`: Expected on public captures (internet scans). `unattributed_leaked_entity_types` must be empty.
5. `leak_evidence`: Check if `leaked_entity_types` matches are `literal` or `normalized`.
6. Validate against the published schema:
   ```bash
   python -c "import json,sys;from jsonschema import Draft202012Validator as V; \
     V(json.load(open('spec/v1.0.0/http-profile.schema.json'))).validate(json.load(open(sys.argv[1])))" your-report.json
   ```
7. Use higher iterations (e.g., 10,000) for performance testing. `--iterations 3` is only a smoke test.

## Step 4: Redacting the Report

The harness does not record capture tokens or API keys, but some fields require manual redaction before publication:

| Field | Action |
| :--- | :--- |
| `target.base_url` | Replace Cloudflare Account ID and Gateway Name with placeholders. |
| `capture.target_must_be_preconfigured_for` | Replace your tunnel hostname with a placeholder to secure your capture server. |
| `capture.self_probe.url` | Generally safe, but replace if it exposes a sensitive host. |
| `capture.self_probe.advertised_url` | Replace tunnel hostname and probe secret. |
| `redaction_claim.configuration_reference` | Keep guardrail IDs (ensure no secrets are included). |

Redact by substituting stable placeholders and noting the redaction in the accompanying `.md` file. Do not delete fields, as this breaks schema validation. Publish the final JSON alongside the target's version, command (with secrets redacted), and configuration.
