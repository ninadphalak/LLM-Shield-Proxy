# LLM-Shield-Proxy HTTP profile configuration

Maintainer working-tree self-test; not an independent result or a release artifact.

- Harness/source label: `fe99dca410930f01aa1addc05354cff9be9bd35e+working-tree`
- Package label: `1.3.4+working-tree`
- Report SHA-256: `f5abc91acd210f722f69662ceb22968998d10d98744b5dc9d0bcf3cc6b1d0ae1`
- Target: Uvicorn on `127.0.0.1:8899`
- Controlled upstream: `http://127.0.0.1:8765/v1` (capture mode: `loopback`)
- Request iterations: 3
- Detector/masking path: default Tier 1/2 configuration and default `SYNTHETIC` masking
- Redaction claim recorded: `claimed`, configured for this run, cited to the project README
- Outcome: `pass` (a verdict, because redaction is claimed AND was enabled)
- External telemetry and anonymous usage tracking: disabled
- Rate limiting, blast-radius limiting, Envoy `ext_proc`, and FinOps metering: disabled for this
  HTTP profile so repeated synthetic requests are not rejected by an orthogonal policy control

Secrets and synthetic request values are not included in the report or this configuration record.
The target used evaluation-only `OVERRIDE_CLIENT_AUTH=true` and injected a non-production key for
the controlled upstream.

Equivalent target environment, with evaluation values substituted locally:

```text
HOST=127.0.0.1
PORT=8899
UPSTREAM_BASE_URL=http://127.0.0.1:8765
UPSTREAM_API_KEY=<controlled-capture-key>
OVERRIDE_CLIENT_AUTH=true
TELEMETRY_ENABLED=false
ANONYMOUS_USAGE_TRACKING=false
ENABLE_EXT_PROC=false
ENABLE_FINOPS_METERING=false
ENABLE_RATE_LIMITING=false
ENABLE_BLAST_RADIUS_LIMITS=false
```

Harness command:

```text
llm-shield-proxy benchmark
  --target-base-url http://127.0.0.1:8899/v1
  --target-api-key <local-evaluation-key>
  --target-name llm-shield-proxy
  --target-version 1.3.4+working-tree
  --iterations 3
  --capture-port 8765
  --json-out benchmarks/results/http-profile-llm-shield-proxy-working-tree.json
```

Regenerate from an exact commit and increase request iterations before presenting this as a
release result or using latency observations comparatively.

Regenerated after the round-7 harness changes: the capture self-probe, the loopback/public
capture mode split, and the removal of the local profile's `latency_measurement` check. That
regeneration is what allowed `response_reconstructed`, `iterations_measured` and
`marker_words_observed_max` to be promoted from optional to `required` in
`spec/v1.0.0/http-profile.schema.json` -- the previously committed artifacts predated all
three, which was the only reason they were optional.
