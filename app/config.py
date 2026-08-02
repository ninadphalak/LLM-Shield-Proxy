import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    UPSTREAM_BASE_URL: str = "https://api.openai.com"
    OPENAI_API_KEY: Optional[str] = None
    REDIS_URL: Optional[str] = None
    SESSION_TTL_SECONDS: int = 3600

    # Telemetry: Strictly Opt-In (Bring Your Own Database)
    TELEMETRY_ENABLED: bool = False
    TELEMETRY_ENDPOINT_URL: Optional[str] = None
    TELEMETRY_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
