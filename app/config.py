import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    UPSTREAM_BASE_URL: str = "https://api.openai.com"
    UPSTREAM_API_KEY: Optional[str] = None
    
    # Enterprise Multi-Provider Support
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    
    # Virtual Key Scoping
    VALID_VIRTUAL_KEYS: str = ""
    valid_virtual_keys_set: frozenset = frozenset()
    
    REDIS_URL: Optional[str] = None
    SESSION_TTL_SECONDS: int = 3600

    # Telemetry: Strictly Opt-In (Bring Your Own Database)
    TELEMETRY_ENABLED: bool = False
    TELEMETRY_ENDPOINT_URL: Optional[str] = None
    TELEMETRY_API_KEY: Optional[str] = None
    
    METRICS_BEARER_TOKEN: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def reload(self):
        import yaml
        import os
        config_path = "config.yaml"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    yaml_config = yaml.safe_load(f)
                    if yaml_config:
                        for k, v in yaml_config.items():
                            if hasattr(self, k.upper()):
                                setattr(self, k.upper(), v)
            except Exception as e:
                pass
        else:
            from dotenv import dotenv_values
            env_vals = dotenv_values(".env")
            for k, v in env_vals.items():
                if hasattr(self, k.upper()) and v is not None:
                    setattr(self, k.upper(), v)
        
        # Atomically update the set reference
        if self.VALID_VIRTUAL_KEYS:
            self.valid_virtual_keys_set = frozenset([k.strip() for k in self.VALID_VIRTUAL_KEYS.split(",") if k.strip()])
        else:
            self.valid_virtual_keys_set = frozenset()

settings = Settings()
settings.reload()
