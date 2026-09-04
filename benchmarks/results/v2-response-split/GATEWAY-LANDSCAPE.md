# Which gateways can even be measured, and why the others cannot

Measuring a gateway with the v2 profile requires it to do something on the response path.
This file records what each candidate actually ships, **verified by inspecting the product
or its published API model**, not by reading marketing pages. It exists so the paper can
say "four measured" without implying the rest were ignored.

Nothing here is a verdict on product quality.

---

## Measured

| Gateway | Redacts response? | Restores caller's values? | Row |
|---|---|---|---|
| LiteLLM 1.99 + Presidio guardrail | yes | no | measured |
| Portkey OSS gateway | no, in the configuration measured | n/a | measured |
| NeMo Guardrails 0.24.0 | detects and truncates | no | measured |
| LLM-Shield-Proxy 1.6.0 | opt-in | **yes** | measured, both configurations |

Plus Google Cloud DLP and Google Model Armor as credentialed **detectors** in this
project's own wrapper, alongside the two Presidio rows.

---

## Ships the capability, cannot be measured here

### Kong Gateway -- `ai-sanitizer`

**The only third-party plugin found that implements BOTH halves of the response split.**
Verified by inspecting `kong/kong-gateway:latest`:

- The plugin directory contains `filters/recover-redacted-response.ljbc`.
- Its schema exposes `recover_redacted` (boolean) and
  `redact_type` (`placeholder` | `synthetic`) -- the same two masking modes
  LLM-Shield-Proxy offers -- plus entity switches for `email`, `ssn`, `phone`,
  `creditcard`, `nationalid`, `passport`, `driverlicense`, `date`, `domain`, `bank`,
  `medical`, `crypto` and custom patterns.

**Two independent gates stop it being measured, both observed rather than inferred.**
Running Kong DB-less with the plugin bound to a route produced:

```
[ai-sanitizer] failed to sanitize request: jsonrpc request failed: timeout
[ai-sanitizer] You are using AI Enterprise Edition plugins but your Kong Enterprise
license does not include AI gateway License. Please contact <support@konghq.com> to
upgrade your license to include AI gateway.
```

1. **An AI Gateway Enterprise licence is required.** The plugin loads and configures in
   free mode; it refuses at request time.
2. **A companion JSON-RPC sanitizer service is required** (`host`, `port`, `scheme` in the
   schema). No public image was found under `kong/ai-sanitizer`, `kong/sanitizer` or
   `kong/kong-ai-sanitizer`.

**This is a finding worth stating in the paper**: the capability the profile tests for --
redact on the way out, restore on the way back -- exists in exactly one commercial gateway
plugin, behind a licence and a service that is not publicly distributed.

Kong also ships `ai-gcp-model-armor`, which routes to the same Google service measured
directly here.

### Higress -- `ai-data-masking`

The plugin ships **in the OSS image** and needs no external registry:
`/usr/share/nginx/html/plugins/ai-data-masking/2.0.1/plugin.wasm`, present in
`higress/all-in-one:latest`. Both the gateway (container port 8080) and the console
(container port 8001) start.

**Not measured**: attaching a WASM plugin to a route requires the console API, which
answers `AuthException: Login required` and whose initialisation endpoint was not found
from outside the image. This is an incomplete configuration on my side, not a limitation
of Higress, and it is the most likely candidate for a fifth measured row.

---

## Does not do the thing, so a row would be uninformative

Verified from each vendor's own API model rather than from documentation prose.

### AWS Bedrock Guardrails -- redacts, does not restore

From the `botocore` service model:

```
GuardrailSensitiveInformationAction = ['BLOCK', 'ANONYMIZE', 'NONE']
GuardrailPiiEntityType             = 31 entity types
ApplyGuardrail                     = present (callable on arbitrary text)
```

There is no restore or recover action. It would land in the redact-only quadrant beside
LiteLLM and NeMo.

**But it is still the strongest candidate for credentials**, for one reason:

```
GuardrailStreamProcessingMode = ['sync', 'async']
```

**AWS ships the buffering-versus-streaming trade-off as a configuration choice.** `sync`
checks before emitting; `async` emits and checks after. That is precisely the trade-off
`DeltaFrag` was built to separate, offered as a product knob, and the profile makes a
testable prediction about it: the two modes should differ on `DeltaFrag` and agree on
`LeakRate(single_chunk)`. A prediction a vendor's own configuration can confirm or refute
is worth more than another redact-only row.

### Azure AI Language -- a detector, not a gateway

From the `azure-ai-textanalytics` SDK: `recognize_pii_entities` returns `redacted_text`
over **220** PII categories. Redaction only; no restore. It is a detector, so it would be a
row like Cloud DLP or Model Armor rather than a gateway row, and it would say something
about Azure's detector rather than about anyone's streaming behaviour.

### Cloudflare AI Gateway -- not verified

No SDK or published API model was available locally to inspect, and no claim is made here
either way. **Do not cite this line as evidence that Cloudflare lacks the capability**; it
records only that this project did not check.

---

## If keys are offered, the order that buys the most

1. **AWS Bedrock Guardrails.** Not for another redact-only row, but for
   `streamProcessingMode: sync|async` -- a vendor-shipped instance of the paper's central
   trade-off, and a prediction the profile can be wrong about.
2. **Kong AI Gateway Enterprise**, if the licence includes the AI add-on and the sanitizer
   service. The only both-halves third-party implementation found.
3. **Cloudflare**, only after checking whether it redacts at all.
4. **Azure AI Language.** Lowest value: a fourth detector row, telling us about a detector
   rather than about a streaming path.

---

## A near-miss worth recording

While testing Kong, `curl http://127.0.0.1:8010/...` returned
`{"error":{"message":"Invalid Proxy API Key"}}` -- which is **LLM-Shield-Proxy's** error
string, not Kong's. Two processes were bound to 8010: Docker's proxy for the Kong
container on `0.0.0.0`, and an unrelated process on `127.0.0.1` that won the lookup.

A conclusion about Kong was one step away from being drawn from a different program's
response. The rule already recorded in `../../litellm-v2-profile/STATUS.md` is what caught
it: **confirm your own process owns the port before trusting anything it returns**
(`netstat -ano | grep ":<port> "`). It has now paid for itself twice.
