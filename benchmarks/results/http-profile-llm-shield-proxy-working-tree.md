# LLM-Shield-Proxy HTTP profile configuration

Maintainer working-tree self-test; not an independent result or a release artifact.

- Harness/source label: `7e959d9d8f9ff6b85e05d9d9ce4642ad3cfb3fed+working-tree`
- Package label: `1.3.4+working-tree`
- Report SHA-256: `792d987b62c39502678fd072a87c8528e2e3b266e0c2963629e52fc0024b785d`
- Target: Uvicorn on `127.0.0.1:8899`
- Controlled upstream: `http://127.0.0.1:8765/v1`
- Request iterations: 3
- Detector/masking path: default Tier 1/2 configuration and default `SYNTHETIC` masking
- External telemetry and anonymous usage tracking: disabled
- Envoy `ext_proc` and FinOps metering: disabled for this HTTP profile

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
