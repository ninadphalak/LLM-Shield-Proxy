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
