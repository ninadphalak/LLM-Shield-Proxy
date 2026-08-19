import pytest

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings


@pytest.fixture(autouse=True)
def test_environment_setup():
    """Ensure consistent environment for tests, disregarding developer .env files."""
    settings.UPSTREAM_BASE_URL = "https://api.openai.com"
    settings.ENABLE_RATE_LIMITING = False
    settings.ENABLE_VAULT_SECRETS = False
    settings.ENABLE_TIER3_ONNX_NER = False
    settings._valid_virtual_keys_set = frozenset()
    settings.VALID_VIRTUAL_KEYS = ""
    settings.SHIELD_ENCRYPTION_KEY = "00" * 32

    from llm_shield_proxy.security.circuit_breaker import circuit_breaker_cache
    circuit_breaker_cache.clear()

    if hasattr(app.state, "http_client"):
        app.state.http_client = None

    from llm_shield_proxy.api.main import app_state
    app_state.is_draining = False
