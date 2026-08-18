"""Enterprise Configuration Module for LLM-Shield-Proxy.

Centralizes, validates, and manages all environment variables, connection pools,
security thresholds, and runtime settings using Pydantic Settings.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional, Set

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)
_config_reload_lock: threading.Lock = threading.Lock()
_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_ENV_FILE_PATH: str = str(_REPO_ROOT / ".env")


class Settings(BaseSettings):
    """Centralized, validated runtime configuration schema for LLM-Shield-Proxy."""

    # Server Configuration
    HOST: str = Field(default="0.0.0.0", description="Proxy listen host")
    PORT: int = Field(default=8000, description="Proxy listen port")
    WORKERS: int = Field(default=1, description="Number of uvicorn worker processes")
    LOG_LEVEL: str = Field(default="INFO", description="Standard logging level")

    # Upstream Provider Configuration
    UPSTREAM_BASE_URL: str = Field(
        default="https://api.openai.com", description="Default upstream LLM provider base URL"
    )
    UPSTREAM_API_KEY: Optional[str] = Field(default=None, description="Fallback upstream API key")
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="Centralized OpenAI API key")
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Centralized Google Gemini API key")
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, description="Centralized Anthropic API key")
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None, description="Centralized DeepSeek API key")

    # Virtual Key Scoping & Multi-Tenancy
    VALID_VIRTUAL_KEYS: str = Field(default="", description="Comma-separated list of authorized virtual API keys")
    ALLOW_CLIENT_UPSTREAM_OVERRIDE: bool = Field(
        default=False, description="Whether to permit clients to override upstream URL via X-Upstream-Base-Url header"
    )

    # Vault & Session Storage Configuration
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
        default=None, description="256-bit AES-GCM encryption key for stateless crypto masking (base64 or hex)"
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
    ENABLE_AGENT_BREAKER: bool = Field(
        default=True, description="Enable Composite Agent Loop Circuit Breaker"
    )
    AGENT_BREAKER_THRESHOLD: int = Field(
        default=3, description="Consecutive duplicate turns before tripping"
    )

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
    MAX_PAYLOAD_SIZE_BYTES: int = Field(
        default=10 * 1024 * 1024, description="Maximum allowed request payload size in bytes (10MB default)"
    )
    MAX_SSE_LINE_LENGTH: int = Field(
        default=1024 * 1024,
        description="Maximum allowed SSE line length in bytes to prevent Slowloris buffer poisoning (1MB default)",
    )
    ENABLE_WATERMARKING: bool = Field(default=False, description="Enable dynamic canary watermarking")
    SHIELD_WATERMARK_SECRET: Optional[str] = Field(default=None, description="Secret for HMAC-SHA256 watermarking")

    # Telemetry & Metrics (Strictly Opt-In)
    TELEMETRY_ENABLED: bool = Field(default=False, description="Enable external audit telemetry event forwarding")
    TELEMETRY_ENDPOINT_URL: Optional[str] = Field(
        default=None, description="Target webhook endpoint URL for audit telemetry"
    )
    TELEMETRY_API_KEY: Optional[str] = Field(default=None, description="Authorization header for telemetry endpoint")
    METRICS_BEARER_TOKEN: Optional[str] = Field(
        default=None, description="Optional Bearer token protecting the /metrics Prometheus endpoint"
    )

    # Internal dynamic cache
    _valid_virtual_keys_set: frozenset[str] = frozenset()

    model_config = SettingsConfigDict(env_file=(_ENV_FILE_PATH, ".env"), env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_watermark_secret(self) -> 'Settings':
        if self.ENABLE_WATERMARKING and not self.SHIELD_WATERMARK_SECRET:
            raise ValueError("SHIELD_WATERMARK_SECRET must be set if ENABLE_WATERMARKING is True.")
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
                            if hasattr(self, attr_name) and v is not None:
                                setattr(self, attr_name, v)
                except Exception as exc:
                    logger.debug("Failed loading .env configuration: %s", exc)

            if self.VALID_VIRTUAL_KEYS:
                keys = [k.strip() for k in self.VALID_VIRTUAL_KEYS.split(",") if k.strip()]
                self._valid_virtual_keys_set = frozenset(keys)
            else:
                self._valid_virtual_keys_set = frozenset()


settings = Settings()
settings.reload()
