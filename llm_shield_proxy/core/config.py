"""Enterprise Configuration Module for LLM-Shield-Proxy.

Centralizes documented environment variables and runtime settings using Pydantic Settings.
"""

from __future__ import annotations

import contextvars
import logging
import os
import secrets
import threading
import types
from pathlib import Path
from typing import Any, Literal, Optional, Set

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)
_config_reload_lock: threading.Lock = threading.Lock()
_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_ENV_FILE_PATH: str = str(_REPO_ROOT / ".env")


class Settings(BaseSettings):
    """Centralized, validated runtime configuration schema for LLM-Shield-Proxy."""

    # AST Parser Protections
    AST_MAX_DEPTH: int = Field(default=40, description="Max allowed depth of JSON payload")
    AST_BRACKET_MULTIPLIER: int = Field(default=10, description="Heuristic multiplier for total brackets vs depth to prevent JSON bombs")

    # Server Configuration
    # Security Note: Binding to 0.0.0.0 is explicitly required for Docker container deployments
    HOST: str = Field(default="0.0.0.0", description="Proxy listen host")  # nosec B104 # noqa: S104
    PORT: int = Field(default=8000, description="Proxy listen port")
    WORKERS: int = Field(default=1, description="Number of uvicorn worker processes")
    LOG_LEVEL: str = Field(default="INFO", description="Standard logging level")
    AUDIT_LOG_FORMAT: Literal["STANDARD", "RFC6902_DIFF"] = Field(
        default="STANDARD", description="Format for audit logs (STANDARD or RFC6902_DIFF)"
    )
    FIPS_STRICT_MODE: bool = Field(default=True, description="Strict fail-closed for FIPS tests")
    AUDIT_SIGNING_PRIVATE_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Ed25519 private key used to sign audit receipts, as a PEM string or a "
            "32-byte seed (base64 or hex). An ephemeral key is generated at startup if unset "
            "(receipts remain internally verifiable but are not stable across restarts)."
        ),
    )
    AUDIT_SIGNING_KEY_FILE: Optional[str] = Field(
        default=None,
        description=(
            "Path to an Ed25519 private key mounted by the operator's secret manager. "
            "Takes precedence over AUDIT_SIGNING_PRIVATE_KEY and fails startup if unreadable or invalid."
        ),
    )
    AUDIT_DURABILITY: Literal["best_effort", "durable", "required"] = Field(
        default="best_effort",
        description=(
            "Audit delivery mode. best_effort preserves non-blocking stdout behavior; durable/required "
            "require AUDIT_DURABLE_PATH and never silently drop records."
        ),
    )
    AUDIT_DURABLE_PATH: Optional[str] = Field(
        default=None,
        description="Append-only JSONL path for durable audit evidence; supports {instance_id} and {pid} tokens.",
    )
    AUDIT_DURABLE_FSYNC: bool = Field(default=True, description="fsync each durable audit record before acknowledgement")
    AUDIT_ENQUEUE_TIMEOUT_SECONDS: float = Field(
        default=5.0, description="Maximum wait for durable/required audit queue acknowledgement"
    )
    ENABLE_EXT_PROC: bool = Field(default=True, description="Enable Envoy ext_proc gRPC hook")
    EXT_PROC_SOCK_PATH: str = Field(
        default="/var/run/llm-shield/ext_proc.sock", description="Path to the ext_proc UDS socket"
    )

    # Upstream Provider Configuration
    DEFAULT_UPSTREAM_PROVIDER: Literal["openai", "azure", "anthropic", "bedrock"] = Field(
        default="openai", description="Default upstream provider type"
    )
    ANTHROPIC_API_VERSION: str = Field(default="2023-06-01", description="Anthropic API version header")
    AWS_REGION: str = Field(default="us-east-1", description="AWS Region for Bedrock")
    UPSTREAM_BASE_URL: str = Field(
        default="https://api.openai.com", description="Default upstream LLM provider base URL"
    )
    UPSTREAM_API_KEY: Optional[str] = Field(default=None, description="Fallback upstream API key")
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="Centralized OpenAI API key")
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Centralized Google Gemini API key")
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, description="Centralized Anthropic API key")
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None, description="Centralized DeepSeek API key")

    # Egress Gateway Configuration
    AIR_GAPPED_MODE: bool = Field(default=False, description="Enable strict Zero-Internet egress gateway mode")
    EGRESS_GATEWAY_URL: Optional[str] = Field(default=None, description="Internal proxy/gateway URL for Air-Gapped mode")
    FORWARD_CLIENT_AUTH: bool = Field(default=False, description="Forward client auth headers in air-gapped mode")
    K8S_WEBHOOK_AUTH_TOKEN: Optional[str] = Field(default=None, description="Optional bearer token for K8s admission webhook")
    K8S_SIDECAR_IMAGE: str = Field(
        default="ghcr.io/ninadphalak/llm-shield-proxy:latest",
        description="Container image appended by the Kubernetes admission webhook",
    )

    # Virtual Key Scoping & Multi-Tenancy
    VALID_VIRTUAL_KEYS: str = Field(default="", description="Comma-separated list of authorized virtual API keys")
    CORS_ALLOWED_ORIGINS: str = Field(default="", description="Comma-separated allowed CORS origins (empty allows same-origin / configured)")
    ALLOW_CLIENT_UPSTREAM_OVERRIDE: bool = Field(
        default=False, description="Whether to permit clients to override upstream URL via X-Upstream-Base-Url header"
    )
    OVERRIDE_CLIENT_AUTH: bool = Field(default=False, description="Strip client auth and inject UPSTREAM_API_KEY")
    ENABLE_OPEN_BYOK_PASSTHROUGH: bool = Field(
        default=False,
        description=(
            "Allow callers presenting an unrecognized key that merely looks like a provider key "
            "(sk-proj-/sk-ant-/AIza prefix) to pass through as BYOK without matching VALID_VIRTUAL_KEYS. "
            "Disabled by default: unauthenticated callers are rejected with 401 unless this is explicitly enabled."
        ),
    )

    # Rate Limiting
    ENABLE_RATE_LIMITING: bool = Field(default=False, description="Enable distributed Token Bucket rate limiter")
    RATE_LIMIT_RPM: int = Field(default=6000, description="Requests per minute per virtual key")
    RATE_LIMIT_BURST: int = Field(default=200, description="Maximum burst size for rate limiter")
    RATE_LIMIT_LOCAL_CACHE_MAXSIZE: int = Field(
        default=50_000,
        description="Maximum local in-memory fallback tenant buckets cached per process before LRU eviction (used when REDIS_URL is unset)",
    )
    RATE_LIMIT_LOCAL_CACHE_TTL_SECONDS: int = Field(
        default=3600,
        description="TTL in seconds for local in-memory fallback tenant buckets (used when REDIS_URL is unset)",
    )

    # Resilience & Failure Modes
    SHIELD_FAILURE_MODE: Literal["FAIL_CLOSED", "FAIL_OPEN"] = Field(
        default="FAIL_CLOSED", description="Default behavior upon engine failure"
    )
    DRAIN_TIMEOUT_SECONDS: int = Field(default=25, description="Max seconds to wait for connection draining on SIGTERM")
    ENABLE_RETRY_FAILOVER: bool = Field(default=True, description="Enable upstream retry and explicit failover logic")
    MAX_RETRIES: int = Field(default=3, description="Maximum transient retry attempts")
    FALLBACK_BASE_URL: Optional[str] = Field(default=None, description="Global fallback provider URL")
    FALLBACK_API_KEY: Optional[str] = Field(default=None, description="Fallback provider API key")

    # mTLS & Custom CA Support
    ENABLE_MTLS: bool = Field(default=False, description="Enable mutual TLS (legacy)")
    SSL_CA_BUNDLE_PATH: Optional[str] = Field(default=None, description="Path to custom CA bundle (legacy)")
    SSL_CLIENT_CERT_PATH: Optional[str] = Field(default=None, description="Path to mTLS client certificate (legacy)")
    SSL_CLIENT_KEY_PATH: Optional[str] = Field(default=None, description="Path to mTLS client key (legacy)")

    # Standardized TLS/SSL Configuration
    TLS_CERT_FILE: Optional[str] = Field(default=None, description="Path to server public certificate for inbound TLS")
    TLS_KEY_FILE: Optional[str] = Field(default=None, description="Path to server private key for inbound TLS")
    CLIENT_CA_FILE: Optional[str] = Field(default=None, description="Path to CA bundle to verify inbound mTLS clients")
    CA_BUNDLE_FILE: Optional[str] = Field(default=None, description="Path to custom CA bundle for outbound upstream verification")
    INSECURE_SKIP_VERIFY: bool = Field(default=False, description="Bypass outbound upstream certificate validation")
    OUTBOUND_CLIENT_CERT: Optional[str] = Field(default=None, description="Path to client certificate for outbound mTLS")
    OUTBOUND_CLIENT_KEY: Optional[str] = Field(default=None, description="Path to client private key for outbound mTLS")

    # HashiCorp Vault Integration
    ENABLE_VAULT_SECRETS: bool = Field(default=False, description="Enable HashiCorp Vault dynamic secrets")
    VAULT_ADDR: Optional[str] = Field(default=None, description="Vault server address")
    VAULT_AUTH_METHOD: Literal["TOKEN", "KUBERNETES", "APPROLE"] = Field(default="TOKEN")
    VAULT_TOKEN: Optional[str] = Field(default=None, description="Direct Vault Token")
    VAULT_ROLE_ID: Optional[str] = Field(default=None, description="AppRole Role ID")
    VAULT_SECRET_ID: Optional[str] = Field(default=None, description="AppRole Secret ID")
    VAULT_K8S_ROLE: Optional[str] = Field(default=None, description="Kubernetes Auth Role")
    VAULT_SECRET_PATH: Optional[str] = Field(default=None, description="KV secret path")
    VAULT_REFRESH_INTERVAL_SECONDS: int = Field(default=300, description="Interval for async secret refresh")

    # Session Storage Configuration
    REDIS_URL: Optional[str] = Field(
        default=None, description="Redis connection URL for distributed vault state (e.g., redis://localhost:6379/0)"
    )
    SESSION_TTL_SECONDS: int = Field(default=3600, description="Time-to-live in seconds for session vault states")
    MAX_SESSION_VAULTS: int = Field(default=10000, description="Maximum capacity of in-memory LRU session vault cache")

    # Redaction & Detection Cascade Settings
    SHIELD_DEFAULT_MASKING_MODE: str = Field(
        default="SYNTHETIC", description="Default masking mode (SYNTHETIC, STRUCTURAL_TAG, SCRUB, STATELESS_CRYPTO)"
    )
    SHIELD_ENCRYPTION_KEY: Optional[str] = Field(
        default=None, description="256-bit AES-GCM encryption key for stateless cryptographic masking (base64 or hex)"
    )
    ENABLE_SYNTHETIC_SWAPPING: bool = Field(
        default=True, description="Enable Faker-based realistic synthetic entity swapping instead of token placeholders"
    )
    ENABLE_TIER2_ENTROPY: bool = Field(
        default=True, description="Enable Tier 2 Shannon Entropy detection for unformatted high-entropy secrets"
    )
    SHANNON_ENTROPY_THRESHOLD: float = Field(
        default=4.5, description="Entropy threshold in bits/symbol (tau_H >= 4.5) for flagging raw secrets"
    )
    SHANNON_MIN_LENGTH: int = Field(default=16, description="Minimum token length to apply Shannon entropy analysis")
    ENABLE_TIER3_ONNX_NER: bool = Field(
        default=False, description="Enable Tier 3 ONNX Runtime contextual Named Entity Recognition"
    )
    ONNX_MODEL_PATH: Optional[str] = Field(
        default=None, description="Filesystem path to quantized ONNX BERT-NER model weights"
    )
    CUSTOM_REGEX_PATH: Optional[str] = Field(
        default=None, description="Path to custom_regex.yaml containing BYOR rules"
    )

    # Agent Circuit Breaker Settings
    ENABLE_AGENT_BREAKER: bool = Field(default=True, description="Enable Composite Agent Loop Circuit Breaker")
    AGENT_BREAKER_THRESHOLD: int = Field(default=3, description="Consecutive duplicate turns before tripping")

    # Blast Radius Limits (Phase 2)
    ENABLE_BLAST_RADIUS_LIMITS: bool = Field(default=False, description="Enable Entity-Weighted Blast Radius Limits")
    BLAST_RADIUS_BURST_CAPACITY: int = Field(default=100, description="Maximum bucket size for PII entity exfiltration limit")
    BLAST_RADIUS_REPLENISH_RATE_PER_MIN: int = Field(default=10, description="Tokens added back per minute to the bucket")


    # HTTP Client & Connection Pooling
    HTTP_TIMEOUT_SECONDS: float = Field(
        default=120.0, description="Total HTTP request timeout for upstream communication in seconds"
    )
    HTTP_CONNECT_TIMEOUT_SECONDS: float = Field(
        default=10.0, description="HTTP connection establishment timeout in seconds"
    )
    HTTP_MAX_KEEPALIVE_CONNECTIONS: int = Field(
        default=10000, description="Maximum keep-alive connections in httpx client pool"
    )
    HTTP_MAX_CONNECTIONS: int = Field(
        default=30000, description="Maximum total concurrent connections in httpx client pool"
    )

    # Security & Buffer Bounds
    AGENT_IDENTITY_ENFORCER: Literal["off", "lenient", "strict"] | bool = Field(default="off", description="Agent Identity Enforcer mode")
    ALLOWED_ISSUERS: list[str] = Field(default_factory=list, description="List of allowed OpenID Connect Issuers")
    ALLOWED_AUDIENCES: list[str] = Field(default_factory=list, description="List of allowed JWT Audiences")
    MAX_PAYLOAD_SIZE_BYTES: int = Field(
        default=10 * 1024 * 1024, description="Maximum allowed request payload size in bytes (10MB default)"
    )
    MAX_SSE_LINE_LENGTH: int = Field(
        default=1024 * 1024,
        description="Maximum allowed SSE line length in bytes to prevent Slowloris buffer poisoning (1MB default)",
    )
    ENABLE_WATERMARKING: bool = Field(default=False, description="Enable dynamic canary watermarking")
    SHIELD_WATERMARK_SECRET: Optional[str] = Field(default=None, description="Secret for HMAC-SHA256 watermarking")

    # Cryptographic Canary Prompt Tripwires
    ENABLE_CANARY_TRIPWIRE: bool = Field(default=False, description="Enable deterministic prompt-extraction tripwire")
    CANARY_TOKEN: Optional[str] = Field(default=None, description="Cryptographic canary string, auto-generated if unset")

    # Telemetry & Metrics (Strictly Opt-In)
    TELEMETRY_ENABLED: bool = Field(default=False, description="Enable external audit telemetry event forwarding")
    TELEMETRY_ENDPOINT_URL: Optional[str] = Field(
        default=None, description="Target webhook endpoint URL for audit telemetry"
    )
    TELEMETRY_API_KEY: Optional[str] = Field(default=None, description="Authorization header for telemetry endpoint")
    METRICS_BEARER_TOKEN: Optional[str] = Field(
        default=None, description="Optional Bearer token protecting the /metrics Prometheus endpoint"
    )
    ANONYMOUS_USAGE_TRACKING: bool = Field(default=True, description="Enable anonymous, opt-out volumetric telemetry")

    # FinOps & Chargeback Metering
    ENABLE_FINOPS_METERING: bool = Field(default=True, description="Enable token metering and FinOps telemetry")

    # Dynamic Policies
    POLICIES_FILE_PATH: str = Field(default="policies.yaml", description="Path to RBAC policies file")
    POLICIES_RELOAD_INTERVAL_SECONDS: int = Field(default=5, description="Interval to check policies file")
    OPA_URL: Optional[str] = Field(default=None, description="Enterprise OPA server URL for RBAC")
    RBAC_CACHE_TTL_SECONDS: int = Field(default=300, description="TTL for stale-while-revalidate RBAC cache")
    MCP_EMPTY_ALLOWLIST_MODE: Literal["DENY_ALL", "BLOCKLIST_ONLY"] = Field(
        default="DENY_ALL",
        description=(
            "MCP tool policy when allowed_tools is empty. DENY_ALL fails closed; "
            "BLOCKLIST_ONLY explicitly permits tools not named in blocked_tools."
        ),
    )

    # Internal dynamic cache
    _valid_virtual_keys_set: frozenset[str] = frozenset()
    _flattened_policies: Any = {}
    _policies_mtime: float = 0.0
    _policies_path: str = ""  # Track last-seen path to invalidate mtime cache on path change

    model_config = SettingsConfigDict(env_file=(_ENV_FILE_PATH, ".env"), env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_watermark_secret(self) -> "Settings":
        if self.ENABLE_WATERMARKING and not self.SHIELD_WATERMARK_SECRET:
            raise ValueError("SHIELD_WATERMARK_SECRET must be set if ENABLE_WATERMARKING is True.")
        return self

    @model_validator(mode="after")
    def validate_canary_token(self) -> "Settings":
        if self.ENABLE_CANARY_TRIPWIRE:
            if not self.SHIELD_WATERMARK_SECRET:
                raise ValueError("SHIELD_WATERMARK_SECRET must be set if ENABLE_CANARY_TRIPWIRE is True.")
            if not self.CANARY_TOKEN:
                self.CANARY_TOKEN = f"[SHIELD_TRIPWIRE_{secrets.token_urlsafe(32)}]"
                logger.info("Generated synthetic Canary Tripwire Token.")
        return self

    @model_validator(mode="after")
    def validate_mtls_paths(self) -> "Settings":
        if self.ENABLE_MTLS:
            if not self.SSL_CLIENT_CERT_PATH or not self.SSL_CLIENT_KEY_PATH:
                raise ValueError("SSL_CLIENT_CERT_PATH and SSL_CLIENT_KEY_PATH must be set if ENABLE_MTLS is True.")
        return self

    @model_validator(mode="after")
    def validate_stateless_crypto_key(self) -> "Settings":
        if self.SHIELD_DEFAULT_MASKING_MODE == "STATELESS_CRYPTO":
            key_src = self.SHIELD_ENCRYPTION_KEY
            if not key_src:
                raise ValueError(
                    "SHIELD_ENCRYPTION_KEY must be set if SHIELD_DEFAULT_MASKING_MODE is STATELESS_CRYPTO."
                )

            import base64

            key_bytes = None
            try:
                key_bytes = base64.b64decode(key_src)
            except Exception:  # nosec B110 noqa: S110
                # Security Note: Fallback decoding attempt without crashing the proxy
                pass

            if key_bytes is None or len(key_bytes) != 32:
                try:
                    key_bytes = bytes.fromhex(key_src)
                except Exception:  # nosec B110 noqa: S110
                # Security Note: Fallback decoding attempt without crashing the proxy
                    pass

            if key_bytes is None or len(key_bytes) != 32:
                raise ValueError("SHIELD_ENCRYPTION_KEY must be a valid 256-bit (32 bytes) base64 or hex string.")

        return self

    @model_validator(mode="after")
    def validate_egress_gateway(self) -> "Settings":
        if self.AIR_GAPPED_MODE and not self.EGRESS_GATEWAY_URL:
            raise ValueError("EGRESS_GATEWAY_URL must be set if AIR_GAPPED_MODE is True.")
        return self

    @property
    def valid_virtual_keys_set(self) -> frozenset[str]:
        """Returns the pre-computed, immutable set of valid virtual keys."""
        return self._valid_virtual_keys_set

    @valid_virtual_keys_set.setter
    def valid_virtual_keys_set(self, keys: Set[str] | frozenset[str]) -> None:
        """Allows direct mutation of valid virtual keys set for testing/dynamic configuration."""
        self._valid_virtual_keys_set = frozenset(keys)

    def reload(self) -> None:
        """Reload configuration from disk (config.yaml or .env) safely."""
        with _config_reload_lock:
            config_path = "config.yaml"
            if os.path.exists(config_path):
                try:
                    import yaml

                    with open(config_path, "r", encoding="utf-8") as f:
                        yaml_config = yaml.safe_load(f)
                        if isinstance(yaml_config, dict):
                            for k, v in yaml_config.items():
                                attr_name = k.upper()
                                if hasattr(self, attr_name) and v is not None:
                                    setattr(self, attr_name, v)
                except Exception as exc:
                    logger.debug("Failed loading YAML configuration: %s", exc)
            else:
                try:
                    from dotenv import dotenv_values, find_dotenv

                    env_path = find_dotenv(usecwd=True) or _ENV_FILE_PATH
                    if os.path.exists(env_path):
                        env_vals = dotenv_values(env_path)
                        for k, v in env_vals.items():
                            attr_name = k.upper()
                            # Process environment is authoritative over dotenv,
                            # matching BaseSettings source precedence.
                            if k not in os.environ and hasattr(self, attr_name) and v is not None:
                                setattr(self, attr_name, v)
                except Exception as exc:
                    logger.debug("Failed loading .env configuration: %s", exc)

            if self.VALID_VIRTUAL_KEYS:
                keys = [k.strip() for k in self.VALID_VIRTUAL_KEYS.split(",") if k.strip()]
                self._valid_virtual_keys_set = frozenset(keys)
            else:
                self._valid_virtual_keys_set = frozenset()

    def reload_policies(self, force: bool = False) -> None:
        """Safely hot-swap the immutable RBAC policy dictionary on file changes.

        Args:
            force: If True, bypasses the mtime/path staleness check and always reloads
                   from disk. Use for explicit reloads (e.g., tests, admin triggers).
                   The background polling loop uses force=False (default) to skip
                   unchanged files efficiently.
        """
        path = self.POLICIES_FILE_PATH
        if not os.path.exists(path):
            return

        if not force:
            current_mtime = os.path.getmtime(path)
            # Invalidate mtime cache if the file path itself has changed (e.g., new temp file in tests)
            if path != self._policies_path:
                self._policies_mtime = 0.0
                self._policies_path = path
            if current_mtime < self._policies_mtime:
                return
        else:
            # Force-reload: reset mtime so next polling call also picks up any subsequent changes
            self._policies_path = path
            self._policies_mtime = 0.0

        if not _config_reload_lock.acquire(blocking=False):
            return
        try:
            import yaml

            with open(path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if not isinstance(yaml_data, dict):
                    return

                roles = yaml_data.get("roles", {})
                virtual_keys = yaml_data.get("virtual_keys", {})

                new_policies = {}

                # Store default role if exists
                default_role_name = yaml_data.get("default_role")
                if default_role_name and default_role_name in roles:
                    new_policies["default_role"] = roles[default_role_name]

                # Flatten virtual keys
                for vk, role_name in virtual_keys.items():
                    if role_name in roles:
                        new_policies[vk] = roles[role_name]

                self._flattened_policies = types.MappingProxyType(new_policies)
                self._policies_mtime = current_mtime
                logger.info("Successfully hot-reloaded policies.yaml")
        except Exception as exc:
            logger.error("Failed loading policies YAML configuration: %s", exc)
        finally:
            _config_reload_lock.release()

request_policy_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("request_policy_ctx", default={})
agent_identity_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agent_identity_ctx", default=None)

class DynamicSettingsProxy:
    """ContextVar-backed proxy for supported per-tenant configuration overrides."""
    def __init__(self, base_settings: Settings):
        object.__setattr__(self, "_base", base_settings)

    def __getattr__(self, name: str) -> Any:
        ctx = request_policy_ctx.get()
        if ctx and name in ctx:
            return ctx[name]
        return getattr(self._base, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._base, name, value)

_global_settings = Settings()
_global_settings.reload()
settings = DynamicSettingsProxy(_global_settings)
