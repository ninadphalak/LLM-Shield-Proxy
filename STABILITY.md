# Stability Tiers

Every feature in this project is assigned a stability tier. The tier answers one question:

> **How much independent evidence exists that this works outside the maintainer's machine?**

Tiers describe *verification status*, not code quality. A feature in Experimental may be
correct; it simply has not been exercised end-to-end against the infrastructure it targets.

| Tier | Meaning | What you can rely on |
|---|---|---|
| **Supported** | Exercised end-to-end in CI against the infrastructure it targets - either because it needs none beyond `pip install llm-shield-proxy`, or because CI provisions the real thing (a Redis server, a Docker container, an HTTP/2 server). Every Supported entry carries a **scoped disclaimer naming exactly what was verified**. | Behavior is reproducible by a third party in minutes, on the versions and topology named in the disclaimer. Breaking changes follow the deprecation policy below. |
| **Beta** | Covered by tests, but the external system it integrates with is substituted in those tests (an in-memory exporter, a mocked backend), or behavior depends heavily on operator configuration. | The code path is exercised. The integration, your topology, and your failure modes are not. Integration-test before production. |
| **Experimental** | Targets infrastructure that has not been exercised end-to-end in this repository, or implements a deliberate subset of a larger protocol. | Treat it as a starting point. Do not place it on a production traffic path without your own validation. |
| **Research** | An exploration that is not on a path to being supported. It may rest on an approach with known limits, or on a protocol the project has not committed to tracking. | **Do not build on these. They may be removed in any release, without a deprecation period or a migration note.** Read them as published notes on an idea, not as a feature. |

A note on the **Supported** definition. It used to read "runs with no external infrastructure",
which made the tier a statement about dependencies rather than about evidence. That produced the
wrong answer for a feature like the Redis vault: it was labeled Experimental not because anything
was known to be wrong with it, but because CI had no Redis. Building the CI is the fix; the tier
now tracks what was actually executed. **A deliberate protocol subset stays Experimental no
matter how well covered it is**, because that label is a scope statement about the protocol, not
a claim about evidence.

## Why this exists

This project is young and has been developed primarily by one maintainer. A long feature list
from a young repository is a reason for suspicion, not confidence. Publishing which features
carry independent evidence - and which do not - is more useful than publishing a longer list.

Features are not removed when they land in Experimental. They are labeled so that you can tell
the difference between "this is proven" and "this is implemented." Research is the one tier
that carries no such promise: entries there may be removed in any release.

Current inventory: **22 Supported, 20 Beta, 11 Experimental, 4 Research (57 total).**

## What CI actually runs

Tiers are only as good as the pipeline behind them, so here is what
`.github/workflows/ci.yml` executes on every push and pull request:

| Job | Provisions | Verifies |
|---|---|---|
| `test-and-lint` (Python 3.11, 3.12) | a **Redis 7** service container | the full suite, including `RedisVaultStore` and the MCP pruner against the real server, HTTP/2 negotiation and multiplexing against a real ALPN server, and OTel span export through a real `BatchSpanProcessor` |
| `container-tests` | Docker on `ubuntu-latest` | the production image builds, starts, serves `/healthz` and `/readyz`, runs as uid 10001, drains in-flight requests on a real SIGTERM and exits 0; and the built wheel installs into a clean venv |
| `tier3-onnx` | a checksum-pinned, cached quantized **DistilBERT multilingual NER** ONNX export | real `onnxruntime` inference through `ONNX_MODEL_PATH` and a real tokenizer, asserted with cases the regex fallback provably cannot produce |
| `chart-verification` | **Helm 3.16** and **promtool 3.1** | both Helm charts lint and render - including the `PrometheusRule`, which the deploy chart could not render at all before this job existed - the rendered alert rules pass `promtool check rules`, alert expressions reference only metrics the app's Prometheus registry actually exports, and rendered probe paths are compared against the app's real routes |

Every job fails rather than skips when its infrastructure is missing: the Redis
step asserts a non-skipped run, and `SHIELD_REQUIRE_DOCKER`, `SHIELD_REQUIRE_ONNX`
and `SHIELD_REQUIRE_HELM` each turn a missing dependency into an error. A green
build with nothing exercised is the failure mode this whole document exists to
prevent.

## Supported

Exercised end-to-end in CI. Each entry names the boundary of what was verified.

**Detection and masking**
- Supported PII and sensitive data types â€” *all ten native Tier 1 detector patterns are exercised with redaction, non-disclosure, and rehydration assertions. Tier 2 entropy candidates and the Tier 3 `PERSON` fallback have separate tests. Model-defined semantic types remain dependent on the operator-supplied model, labels, thresholds, language, and corpus.*
- Tier 1 pre-compiled built-in regex engine
- Tier 2 Shannon entropy scanner
- Format-preserving synthetic masking
- JSON bomb / payload nesting depth protection
- Dynamic schema rewriting (OpenAI tool schemas)
- Tier 3 quantized ONNX BERT-NER - *verified in CI with a checksum-pinned quantized DistilBERT multilingual NER export: the session and tokenizer load, `onnxruntime` inference runs, and detections are asserted with cases the regex fallback cannot produce (single-token surnames, non-ASCII names) plus one it gets wrong (title-case phrases). **Two real constraints, asserted in the tests:** `detect_spans` feeds only `get_inputs()[0]` and `[1]`, so a three-input BERT export that also wants `token_type_ids` raises and is silently downgraded to the regex fallback - model choice is a compatibility constraint; and every non-`O` prediction is labelled `PERSON` without reading the model's `id2label`, so a location or organisation is reported as a person. Accuracy, latency and memory depend on the model you actually choose.*
- Redis TTL session vault - *verified against Redis 7 in CI: cross-instance rehydration, server-side rolling TTLs, tenant key isolation, corrupt-payload degradation and session clearing. Other Redis versions, Cluster/Sentinel topologies, TLS/ACL configurations and eviction policies are untested. Mappings are stored as plaintext JSON.*

**Streaming**
- SSE sliding-window rehydration buffer
- Bounded streaming JSON lexer
- HTTP/2 upstream connection pooling - *verified in CI against a TLS ALPN server: the proxy's startup-built client negotiates `h2`, eight concurrent proxied requests multiplex over a single upstream TCP connection, and an `http/1.1`-only upstream still works. Measured against hypercorn, not against a commercial provider endpoint. `get_http_client`'s lazy fallback used to build an `http2=False` client, so a request served before or after the lifespan client existed silently dropped to HTTP/1.1; both paths now construct through a single `build_upstream_client()` factory, and CI asserts the two clients' pool configuration is identical.*

**Runtime and operations**
- Graceful shutdown / pod drain - *verified in CI against the built container image: a real SIGTERM to PID 1 lets an 8-second in-flight request finish and return a correctly rehydrated body, new connections stop being accepted, and the process exits 0 rather than being SIGKILLed. Single-process only; the drain counter does not coordinate across `WORKERS > 1`, and no Kubernetes pod lifecycle (preStop hook, endpoint removal) has been exercised.*
- Request-ID correlation and sanitization - *verified in CI on real responses: a UUID4 is generated when absent, allowlisted inbound IDs are propagated, and hostile IDs (CRLF, oversized, empty) are replaced rather than reflected - on proxied, streaming, probe, 401 and sanitized-500 responses. **Not** returned on the drain 429, which is built before an ID is assigned.*
- Security response headers - *verified in CI on success, 401, streaming, probe and sanitized-500 responses; the 500 handler stamps them itself, because Starlette builds that response outside the application middleware stack. **Not** set on the drain 429.*
- Component health probes and Prometheus alert rules - *verified in CI: `/readyz`'s Redis component against a live Redis 7, `/healthz` and `/readyz` against the running container image, and both Helm charts rendered with real `helm` 3.16 - the rendered probe paths are compared against the routes the application actually serves, and the rendered alert rules pass real `promtool` 3.1 and reference only metrics the app's Prometheus registry exports. Rendering the charts for the first time found two blocking defects, both now fixed: the deploy chart's `PrometheusRule` did not render at all, and its Deployment probed `/health/ready` and `/health/live`, which the application does not serve. **No pod has been scheduled from either chart and no Prometheus server has evaluated these rules**, so kubelet probe behavior, pod lifecycle and live alert firing remain untested.*

**Conformance and evidence**
- Streaming privacy conformance harness - local in-process profile (`llm-shield-proxy benchmark`) and the endpoint-neutral HTTP profile, which ships as its own distribution, `pii-leak-benchmark`
- Tamper-evident audit hash chaining
- Ed25519-signed audit receipts and chain verification
- FIPS KAT self-tests and RFC 6902 differential audit records
- NIST OSCAL assessment-results generation
- Compliance-pack CLI export

**Security**
- SSRF / DNS-rebinding egress guard

## Beta

Exercised by tests, but with the external system substituted, or dependent on substantial
operator configuration.

- Asynchronous OpenTelemetry tracing - *span export is now verified through a real
  `BatchSpanProcessor` into an `InMemorySpanExporter`: the request span, the nested detection-tier
  span and the streaming `buffer_flush` span are all exported, an inbound `traceparent` is adopted
  as the parent, and no PII appears in any serialized span. The OTLP-over-HTTP transport and a
  real collector are still not exercised, which is why this is Beta and not Supported.*
- Stateless mutation engine (requires an operator-supplied `SHIELD_ENCRYPTION_KEY`)
- In-band stateless cryptographic masking (requires an operator-supplied `SHIELD_ENCRYPTION_KEY`)
- Granular entity policy scopes
- 4-mode per-request masking pipeline (the `STATELESS_CRYPTO` mode requires an operator-supplied key)
- Bring-your-own-regex custom rules (requires an operator-supplied YAML rules file)
- Provider failover with per-request override
- FinOps `stream_options` injection
- Edge-level agent identity enforcer (JWT / DPoP)
- Canary prompt tripwires (requires `SHIELD_WATERMARK_SECRET`)
- Entity-weighted request limits
- LLM FinOps meter
- Provider failover routing
- Bounded exponential retries (requires a configured upstream)
- Composite agent-loop circuit breaker
- Request rate limiting and traffic-engineering controls
- Role-based policy-as-code with hot reload
- Request-scoped dynamic overrides
- Stream digest receipt
- Applied role name in audit events

## Experimental

Not exercised end-to-end against the infrastructure it targets, or a deliberate protocol subset.

| Feature | Why it is Experimental |
|---|---|
| Multi-provider request/event translators | Provider selection and the Anthropic transformer are unit-tested, but the implementation is a documented subset and is not exercised against provider protocols. |
| Anthropic adapter | The adapter deliberately handles a text-focused subset, coerces unsupported roles, and is not validated against the Anthropic API. |
| Envoy `ext_proc` integration | Not validated against a pinned real Envoy container. Buffer modes, timeout policy, and long-TTFT behavior are unverified. |
| UDS socket TOCTOU hardening | Linux-only and coupled to the `ext_proc` path above. |
| Kubernetes mutating webhook | `helm template --set webhook.enabled=true` now renders in CI, and the render is checked for one shared CA across the serving certificate and the admission `caBundle` and for the `/v1/k8s/mutate` path matching the app route. No cluster admission install has been performed. |
| HashiCorp Vault secrets and mTLS | No live Vault backend has been exercised. |
| OPA and Vault RBAC resolvers | Failure, staleness, refresh, and concurrency behavior are untested against live backends. |
| Decision trace exporter | A library primitive. Runtime proxy routes do not invoke it. |
| GRC webhook and file transport | Caller-wired primitives with no environment-based wiring or vendor connector. |
| Multi-provider upstream key registry | Matches four exact hostnames. Azure hostname/header handling is not implemented. |
| Reproducible benchmarks and signed supply chain | The local benchmark is covered separately by the supported conformance harness. Cosign/OIDC signing and SBOM attestation live in CI workflows and have no implementing package module or covering test under `tests/`. |

## Research

Explorations, not features. **Anything here may be removed in any release, without a
deprecation period and without a migration note.** They are published because the work is real
and the notes may be useful, not because they are on a path to support.

| Exploration | What it is, and what limits it |
|---|---|
| Zero-width correlation marker | Injects zero-width Unicode characters keyed with the operator-supplied `SHIELD_WATERMARK_SECRET`, for internal correlation. Enabling watermarking without the secret fails startup, and different secrets produce different identity fingerprints for the same credential. Zero-width marks do not survive most normalization, copy-paste, JSON re-encoding, or markdown rendering, and must not be injected into code or structured output. Treat it as a tracing aid for non-adversarial paths, not as a control against an adversary who can strip characters. |
| Scoped MCP JSON-RPC gateway and pluggable tool-call RBAC | An exploration of brokering `tools/list`, `tools/call`, and `resources/read` inside a controlled network boundary, with no initialization, capability negotiation, sessions, or GET/SSE channel. It is not drop-in for any MCP SDK. Empty allowlists deny every tool by default; intentional blocklist-only deployments must set `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY`, which emits a critical startup warning that every unblocked tool is permitted. MCP is still moving and this project has not committed to tracking it. |
| Context-aware tool catalog pruner | Its Redis path **is** now verified against Redis 7 in CI (cache write with a clamped TTL, cache hit, per-tenant isolation, policy-version invalidation, including a tenant's first invalidation). It sits here as a scope statement rather than an evidence one: it serves the `tools/list` method of a protocol subset this project has not committed to tracking. |
| Dynamic MCP tool schema rewriting | Schema rewriting cannot compel a model or parser to echo the added fields. |

## Defects found by building the CI

Running these paths for the first time turned up five defects. **All five are now fixed**, and
every one is held by a test that fails if the behavior regresses. They are recorded here because
the tier table above is a claim about evidence, and the evidence includes what the evidence-
gathering itself found.

| Defect | Effect | Now held by |
|---|---|---|
| `deploy/helm/llm-shield-proxy`'s Deployment probed `/health/ready` and `/health/live`; the application serves `/readyz`, `/livez`, `/health` and `/healthz`. | Both probes fell through to the authenticated catch-all and never returned 200, so a pod deployed with that chart never became Ready and was then restarted by the liveness probe. The chart could not deploy at all. | `tests/test_helm_render_and_alerts.py::test_deploy_chart_probe_paths_are_routes_the_application_serves` |
| `deploy/helm/llm-shield-proxy/templates/prometheus-rule.yaml` called `include "llm-shield-proxy.labels"`, but that chart's `_helpers.tpl` defined only `.name` and `.fullname`. | `helm template --set prometheus.prometheusRule.enabled=true` failed outright, so the documented alert rules could not be installed from the chart as shipped. `helm lint` passed because the rule is disabled by default, and the old test stripped the template tags before parsing. | `tests/test_helm_render_and_alerts.py::test_shipped_chart_renders_the_prometheus_rule`, `::test_rendered_prometheus_rule_carries_the_common_labels` |
| `get_http_client`'s lazy fallback constructed its client with `http2=False` while the lifespan pool used `http2=True`, from two copies of the same configuration block. | Any request served by the fallback rather than the lifespan-built pool silently dropped to HTTP/1.1, with no error and no log line. This is also why the pre-existing suite never observed HTTP/2: `conftest.py` clears `app.state.http_client` before each test. | `tests/test_http2_upstream.py::test_lazy_fallback_client_matches_the_pooled_client`, `::test_only_one_place_in_main_constructs_the_upstream_client` |
| The sanitized 500 response is produced by Starlette's `ServerErrorMiddleware`, which sits *outside* the application middleware stack, so it never passed back through `security_and_tracing_middleware`. | An unhandled 500 carried no `X-Content-Type-Options`, `X-Frame-Options` or `Strict-Transport-Security`, and no `X-Request-ID`, so a caller holding a failed request could not quote a correlation ID back to an operator. | `tests/test_response_headers.py::test_security_headers_survive_the_sanitized_500_handler`, `::test_request_id_is_returned_on_the_500_path` |
| The MCP pruner read `mcp:policy_version:{tenant}` and substituted `"1"` when the key was absent, while `INCR` on that same absent key also yields `1`. | The **first** `notifications/tools/list_changed` a tenant ever sent did not change the cache key, so a stale tool catalog kept being served for up to the cached TTL. Every subsequent notification invalidated correctly, which is what made it easy to miss. | `tests/test_redis_integration.py::test_first_list_changed_notification_invalidates` |

One related behavior is documented rather than changed: the drain short-circuit returns its 429
before a request ID is assigned, so that response carries neither the security headers nor an
`X-Request-ID`. `tests/test_response_headers.py::test_security_headers_on_the_drain_rejection`
pins what a draining pod actually sends.

## Deprecation policy

- **Supported** features change behavior only in a minor release, with a documented migration
  note, and never in a patch release.
- **Beta** features may change behavior in a minor release with a changelog entry.
- **Experimental** features may change or be removed in any release.
- **Research** entries carry no stability commitment of any kind and may be removed
  without notice. They are not part of the supported surface.

The conformance specification in `spec/` has its own versioning and governance process and is
not covered by this policy. See `website/docs/conformance/governance.md`.

## Moving a feature between tiers

A feature moves up when independent evidence exists - a passing end-to-end test against the real
infrastructure in CI, or a reproducible deployment by someone other than the maintainer. Open an
issue with the evidence and the tier will be updated.
