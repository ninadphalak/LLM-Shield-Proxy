---
sidebar_position: 7
---

# Runbook: measuring a hosted gateway

Executable start to finish by someone who has not read the design discussion. It covers
Cloudflare AI Gateway and Portkey, the two hosted targets in the comparison table.

Everything here that does not need a vendor account has been run. Everything that does
is marked **NOT RUN** and is drawn from vendor documentation, cited inline. Do not
convert a row in [the results table](./results) from `Not run` until you have the
pinned configuration and the raw artifact.

## Before anything else: what your row is allowed to say

Read this first. It decides whether the run you are about to do can be published at all.

A measurement is not a verdict. The harness derives an `outcome` field from what the
product **claims** about PII redaction, what you **configured**, and only then from what
it measured. You supply the claim; you cannot type the outcome, and the published schema
re-derives it, so a hand-edited report fails validation.

| `outcome` | Means | Publish it as |
| :--- | :--- | :--- |
| `pass` | Claims redaction, it was enabled, every check passed | Pass |
| `fail` | **Protected data reached the capture origin.** The only leak finding | Fail — with `leak_evidence` |
| `no-leak-profile-not-met` | Nothing leaked, but a behavioural check failed — usually one-way anonymization that never restores the value | "No leak; does not meet the reversible-masking requirement". **Never as a privacy failure** |
| `not-applicable` | The product does not claim PII redaction at all | "Not applicable — no redaction feature offered" |
| `redaction-not-enabled` | It offers redaction; you did not turn it on | A configuration statement. Enable it and re-run |
| `inconclusive` | Nothing correlated — the target never reached your capture | Not a row. Fix the configuration and re-run |
| `claim-unstated` | You did not record the claim | Not a row |

Two traps this table exists to prevent, both already reproduced:

- **Cloudflare AI Gateway does not claim PII redaction.** Its DLP feature detects and
  then either *flags* (["the original response is returned to the
  client"](https://developers.cloudflare.com/ai-gateway/features/dlp/)) or *blocks*
  (replaced with a 400). Neither redacts. Scoring it "Fail" on a privacy benchmark
  measures it against something it never offered.
- **Portkey redacts but does not rehydrate.** Its PII Redaction replaces values with
  [`{{EMAIL_ADDRESS_1}}`-style
  identifiers](https://docs.portkey.ai/docs/product/guardrails/pii-redaction) and
  documents no restoration, so `response_fidelity` fails while nothing leaks. That is
  `no-leak-profile-not-met`, not a Fail. Presidio's `replace`/`hash`/`mask` behave the
  same way.

## Step 1 — a public capture (no vendor account needed)

A hosted gateway cannot reach your loopback. You expose the capture yourself; this
project does not operate a capture service, because a referee that hosts the traffic it
measures is not neutral.

Bind the capture to **loopback** and let the tunnel reach it. The port is then never
directly exposed to the internet, only the tunnel hostname is.

```bash
# 1. Install the harness. Base install is stdlib + httpx -- no gateway, no proxy stack.
python -m pip install llm-shield-proxy
python -m pip install jsonschema          # optional, to validate the artifact yourself

# 2. Start a tunnel. Cloudflare quick tunnels need NO account.
cloudflared tunnel --url http://localhost:8765
# -> prints e.g. https://your-quick-tunnel.trycloudflare.com
```

```bash
# 3. Choose a capture token and export it. Use the ENVIRONMENT VARIABLE, not the flag:
#    process listings show argv, so --capture-token is readable by other users.
export CONFORMANCE_CAPTURE_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
echo "$CONFORMANCE_CAPTURE_TOKEN"   # you will paste this into the vendor as the upstream API key
```

**Verified end to end** on 2026-09-01 through a real `trycloudflare.com` quick tunnel
against a local gateway: the probe validated the advertised URL through the tunnel
(`advertised_url_reachable: true`), all three iterations correlated, nothing leaked, and
the token stayed out of the artifact.

### What the tunnel does to the request — measured

Relevant because the tunnel sits in the path on every hosted row.

| Property | Result |
| :--- | :--- |
| HTTP version at the capture | HTTP/1.1. No HTTP/2 downgrade problem — cloudflared speaks HTTP/1.1 to the origin |
| Transfer framing | `content-length` preserved. **No chunked re-framing** |
| Body | Byte-identical to the direct request |
| `Authorization` | **Survives intact** — this is what makes token attribution work |
| Headers added | 10: `cf-ray`, `cf-connecting-ip`, `cf-ipcountry`, `cf-visitor`, `cf-warp-tag-id`, `cf-ew-via`, `cf-worker`, `cdn-loop`, `x-forwarded-for`, `x-forwarded-proto` |
| Headers removed | none |
| Effect on leak matching | Digits per request rise from 20 to 93, but **needle proximity is unchanged** (SSN 2 of 9 direct and tunnelled) |

One consequence is enumerated rather than suppressed. The capture inspects request
headers as an egress channel, because a gateway could hide values there. Exactly one
valid IPv4 address — `123.45.67.89` — normalizes to the same digits as the SSN fixture,
so a client connecting from it produces an SSN finding against a gateway that redacted
correctly. Suppressing header inspection to remove that would open a real evasion
channel, so instead every finding publishes `leak_evidence` with `match: literal` or
`match: normalized`. **Check that field before calling anything a leak.** The 16-digit
card needle is unreachable by any IPv4.

## Step 2a — Cloudflare AI Gateway  **(NOT RUN — needs an account)**

**Account tier.** [AI Gateway is available on all
plans](https://developers.cloudflare.com/ai-gateway/reference/pricing/) and its core
features are free; you need a Cloudflare account and an API token with `AI Gateway -
Edit`. **Custom Providers is Beta.** The free Workers plan is sufficient for the
gateway itself.

**Point it at your tunnel.** Cloudflare requires a
[Custom Provider](https://developers.cloudflare.com/ai-gateway/configuration/custom-providers/)
whose `base_url` **must start with `https://`** — which the tunnel gives you.

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

Then send the capture token as the provider credential so the capture can attribute the
traffic, and run:

```bash
llm-shield-conformance \
  --target-base-url "https://gateway.ai.cloudflare.com/v1/$CF_ACCOUNT_ID/$CF_GATEWAY/conformance-capture" \
  --target-api-key "$CONFORMANCE_CAPTURE_TOKEN" \
  --target-name "cloudflare-ai-gateway" \
  --target-version "<dashboard build/date you observed>" \
  --iterations 3 \
  --capture-port 8765 \
  --capture-public-url "https://your-quick-tunnel.trycloudflare.com/v1" \
  --redaction-claimed not-offered \
  --redaction-claim-citation "https://developers.cloudflare.com/ai-gateway/features/dlp/ (DLP actions are Flag and Block; Flag returns the original response, Block returns a 400. Neither redacts.)" \
  --json-out cloudflare-ai-gateway.json
```

**Expect `outcome: not-applicable`.** The fixture will reach your capture and
`leaked_entity_types` will list all three entities. That is a correct measurement of a
product that never offered redaction, and it is **not** a failure. Publish the row as
"Not applicable — detects and blocks, does not redact".

If you additionally enable DLP with the **Block** action, expect `captured_requests: 0`
and `outcome: inconclusive`: the request never reaches the upstream. That is also not a
Fail — it is the gateway doing exactly what Block means. Say so in the row rather than
publishing an `inconclusive` artifact as a result.

## Step 2b — Portkey  **(NOT RUN — needs an account)**

**Account tier.** [Guardrails are on all
plans](https://docs.portkey.ai/docs/product/guardrails), but the tiers differ:

| Plan | Guardrails available | Enough to run this? |
| :--- | :--- | :--- |
| Developer (free) | `BASIC` only | **Yes** — `Regex Match` is BASIC and supports the Redact toggle |
| Production | `BASIC`, `PARTNER`, `PRO` | Yes — includes the `Portkey Pro PII` detector |
| Enterprise | all + `custom` | Yes |

Two materially different runs are possible, and a row **must say which**:

- **Portkey Pro PII** (Production plan) — Portkey's own detector. Redacts `Phone number`,
  `Email addresses`, `Location information`, `IP addresses`, `SSN`, `Names`,
  `Credit card information`, which covers all three fixture entity types. This measures
  *Portkey's detector*.
- **Regex Match** (free Developer plan) — you supply the patterns. This measures
  *Portkey's gateway and guardrail engine with tester-authored patterns*, **not**
  Portkey's detection quality. Say that in `--redaction-config-reference`, or the row
  claims something it did not measure.

**Enabling PII redaction** (required — a run without it is
`redaction-not-enabled`, not a verdict):

1. Go to **Guardrails → Create**.
2. Either pick a PII guardrail and turn on the **Redact PII** toggle, or pick
   **Regex Match** (BASIC) and for each pattern set the rule, a replacement, and toggle
   **Redact** to ON. Portkey documents these patterns:
   `\b\d{3}-\d{2}-\d{4}\b` (SSN), `\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b` (card),
   `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b` (email).
3. Save and copy the **Guardrail ID**.
4. Attach it to a config as a `before_request_hooks` entry.

**Pointing it at your tunnel needs no dashboard step.** `custom_host` is a request
header, so the harness can set it itself. Note that
[Portkey blocks private and reserved IP ranges by
default](https://portkey.ai/docs/integrations/llms/byollm) — a tunnel hostname works,
loopback never will.

```bash
llm-shield-conformance \
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
  --redaction-config-reference "<Portkey Pro PII guardrail id ...>, Redact PII toggle ON, attached via before_request_hooks" \
  --json-out portkey.json
```

`x-portkey-forward-headers=Authorization` is what passes the capture token through to
your tunnel unprocessed, so the capture can attribute the traffic.

**Expect `outcome: no-leak-profile-not-met`.** Portkey's redaction replaces values with
`{{EMAIL_ADDRESS_1}}`-style identifiers and documents no rehydration, so
`configured_upstream_boundary` should pass with `leaked_entity_types: []` while
`response_fidelity` and `fragmentation_safety` fail. Publish that as "no leak; does not
meet the reversible-masking requirement". **A `fail` here would be wrong and you should
investigate the run before believing it.**

## Step 3 — before you publish the row

Check all of these against the artifact:

1. `outcome` — and read `outcome_rationale`. If it is not `pass`, `fail`, or
   `no-leak-profile-not-met`, it is not a row.
2. `capture.self_probe.advertised_url_reachable` is `true`. If false, the target may
   never have reached you; `captured_requests: 0` cannot distinguish that from a leak.
3. `checks.configured_upstream_boundary.correlated_requests` equals your `--iterations`.
4. `unattributed_requests` — expected to be non-zero on a public capture (internet scan
   traffic). It does not fail the check. `unattributed_leaked_entity_types` **does**.
5. `leak_evidence` — for every entity in `leaked_entity_types`, is the match `literal`
   or `normalized`? A normalized-only match in the `headers` channel deserves a second
   look before you publish an accusation.
6. `needle_proximity` against `needle_lengths` — equal means the value was present.
7. Validate against the published schema:
   ```bash
   python -c "import json,sys;from jsonschema import Draft202012Validator as V; \
     V(json.load(open('spec/v1.0.0/http-profile.schema.json'))).validate(json.load(open(sys.argv[1])))" your-report.json
   ```
8. `--iterations 3` is a smoke test. Raise it before presenting latency comparatively.

## Step 4 — redact before committing

The harness never writes the capture token, the target API key, or extra header
**values** into the report (only header *names*). These it does write, and you must
decide about them:

| Field | Contains | Action |
| :--- | :--- | :--- |
| `target.base_url` | For Cloudflare, **your account ID and gateway name** | Replace with a placeholder |
| `capture.target_must_be_preconfigured_for` | Your tunnel hostname | Replace — a quick-tunnel hostname is ephemeral, but publishing it while the run is live exposes your capture |
| `capture.self_probe.url` | Your local bind address and port | Usually fine; replace if it is a real host |
| `capture.self_probe.advertised_url` | Tunnel hostname + the probe secret | Replace |
| `redaction_claim.configuration_reference` | Guardrail IDs | Keep — the row must reproduce. Do not include secrets in it |

Redact by substituting a stable placeholder, and say in the accompanying `.md` that you
did. Do not delete the fields: the schema requires them, and a report that no longer
validates is not a publishable artifact.

Publish alongside the JSON: the target's version/build as observed, the exact command
with credentials replaced, the guardrail configuration, and every failed or discarded
run. See [governance](./governance).
