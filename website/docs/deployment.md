[⬅️ Back to README](/)

# Deployment Configuration

LLM-Shield-Proxy is configured entirely via environment variables, following 12-factor app principles. Configuration governs upstream routing, cryptographic keys, performance thresholds, and security policies.

## Complete Configuration Reference

### Core Proxy Settings
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | IP address to bind the server. |
| `PORT` | `8000` | Port to bind the server. |
| `WORKERS` | `1` | Number of Uvicorn worker processes. |
| `LOG_LEVEL` | `INFO` | Application log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `UPSTREAM_BASE_URL` | `https://api.openai.com` | The base URL of the target LLM provider. |

### API Keys
Provide the central API keys the proxy will use to authenticate with upstream providers.
| Environment Variable | Description |
| :--- | :--- |
| `OPENAI_API_KEY` | OpenAI API key. |
| `ANTHROPIC_API_KEY` | Anthropic API key. |
| `GEMINI_API_KEY` | Google Gemini API key. |
| `DEEPSEEK_API_KEY` | DeepSeek API key. |

### Security & Authentication
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `VALID_VIRTUAL_KEYS` | `""` | Comma-separated list of authorized client tokens. |
| `ENABLE_OPEN_BYOK_PASSTHROUGH` | `False` | Allow unrecognized provider-formatted keys (e.g. `sk-proj-...`) to pass through to the upstream provider. |
| `OVERRIDE_CLIENT_AUTH` | `False` | Strip the client's API key and replace it with the proxy's configured upstream API key. |
| `CORS_ALLOWED_ORIGINS` | `""` | Comma-separated list of allowed browser origins for CORS. Leave empty for strict isolation. |
| `SHIELD_FAILURE_MODE` | `FAIL_CLOSED` | Security posture on unexpected errors (`FAIL_CLOSED` terminates, `FAIL_OPEN` bypasses). |

### PII Redaction Engines
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_TIER2_ENTROPY` | `True` | Enable Shannon entropy scanning for unstructured secrets. |
| `SHANNON_ENTROPY_THRESHOLD` | `4.5` | Minimum entropy bits/char to trigger detection. |
| `ENABLE_TIER3_ONNX_NER` | `False` | Enable local ONNX BERT-NER processing. |
| `ONNX_MODEL_PATH` | `None` | Path to the ONNX model weights. |
| `CUSTOM_REGEX_PATH` | `None` | Path to `custom_regex.yaml` for Bring-Your-Own-Regex rules. |
| `ENABLE_SYNTHETIC_SWAPPING` | `True` | Replace detected entities with format-preserving synthetic data. |
| `SHIELD_DEFAULT_MASKING_MODE` | `SYNTHETIC` | The default masking strategy (`SYNTHETIC`, `SCRUB`, `STATELESS_CRYPTO`). |
| `ENABLE_DEEP_PAYLOAD_REDACTION` | `True` | Walk fields outside the known chat shapes: `metadata`, `user`, `tools`, `response_format`, and any field this proxy does not know by name. Turning it off lets those fields reach the provider unredacted. |
| `PAYLOAD_PROTECTED_KEYS` | `""` | Extra JSON keys deep redaction must never rewrite, comma separated, added to the built-in structural set (`model`, `type`, `enum`, `$ref`, and similar). Per-role equivalent: `payload_skip_keys`. |
| `PAYLOAD_MAX_REDACT_STRING_LENGTH` | `8192` | Strings longer than this, and any `data:` URI, are forwarded without inspection. |
| `UNMAPPED_BLOB_POLICY` | `warn` | What happens when a string past that ceiling appears in a field no policy claims. `skip` forwards it silently, `warn` forwards it and writes a signed audit record naming the JSON path, `block` rejects the request with `HTTP 413`. |

#### Rolling out deep redaction

Deep redaction is on by default because a passthrough proxy forwards every field, so
any field left unwalked is egressed. Two settings control what it costs.

Start on `UNMAPPED_BLOB_POLICY=warn`. Every blob in an unclaimed field is forwarded
and audited as `UNMAPPED_BLOB_FORWARDED` with its JSON path. Collect those paths, add
them to the owning role's `payload_skip_keys`, then move to `block` once the audit
trail is quiet.

Both `UNMAPPED_BLOB_POLICY` and `PAYLOAD_MAX_REDACT_STRING_LENGTH` can be set per role
in `policies.yaml`, so one tenant can run on `block` while another is still onboarding.

Raising `PAYLOAD_MAX_REDACT_STRING_LENGTH` past your largest attachment is the one
change here that visibly slows the proxy, because the walk then scans base64 that no
text detector can match. Measure it on your own payload shapes:

```bash
python benchmarks/payload_walk_latency.py --turns 200 --image-mb 4
```

The `blob cost` column is what raising the ceiling would cost you. These are local
microbenchmarks; they exclude network, TLS and model time, so use them to compare
settings against each other rather than as an end-to-end latency figure.

### Cryptography & Auditing
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `SHIELD_ENCRYPTION_KEY` | `None` | 256-bit AES-GCM key used for `STATELESS_CRYPTO` masking and stream digest receipts. |
| `AUDIT_DURABILITY` | `best_effort` | Durability mode (`best_effort`, `durable`, `required`). |
| `AUDIT_DURABLE_PATH` | `None` | File path for append-only JSONL logs (required for durable modes). |
| `AUDIT_SIGNING_KEY_FILE` | `None` | Path to Ed25519 private key for signing audit logs. |

### Distributed State & Resiliency
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `REDIS_URL` | `None` | Connection string for Redis (used for distributed state and rate limiting). |
| `SESSION_TTL_SECONDS` | `3600` | Expiration time (in seconds) for stateful PII mappings. |
| `ENABLE_RATE_LIMITING` | `False` | Enable Redis-backed token bucket rate limiting. |
| `RATE_LIMIT_RPM` | `6000` | Maximum requests per minute per virtual key. |
| `MAX_RETRIES` | `3` | Maximum automatic retries for transient upstream errors. |
| `FALLBACK_BASE_URL` | `None` | Secondary provider URL for automatic failover. |

### Advanced Integrations
| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `POLICIES_FILE_PATH` | `policies.yaml` | Path to the YAML file containing RBAC roles and tool policies. |
| `ENABLE_EXT_PROC` | `True` | Enable the Envoy gRPC External Processing listener. |
| `EXT_PROC_SOCK_PATH` | `/var/run/llm-shield/ext_proc.sock`| Unix Domain Socket path for Envoy `ext_proc`. |
| `TELEMETRY_ENABLED` | `False` | Enable OpenTelemetry (OTLP) tracing exports. |
| `ENABLE_VAULT_SECRETS` | `False` | Enable HashiCorp Vault integration for dynamic secrets. |

## TLS/HTTPS Deployment

In production, run the proxy behind TLS. You can terminate TLS directly at the proxy using standard certificate files:

```bash
llm-shield-proxy --tls-cert-file /etc/ssl/certs/server.crt --tls-key-file /etc/ssl/private/server.key
```

### Docker Compose Example
```yaml
services:
  llm-shield:
    image: llm-shield-proxy:latest
    ports:
      - "8443:8000"
    environment:
      - UPSTREAM_BASE_URL=https://api.openai.com
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TLS_CERT_FILE=/certs/server.crt
      - TLS_KEY_FILE=/certs/server.key
    volumes:
      - ./certs:/certs:ro
```
