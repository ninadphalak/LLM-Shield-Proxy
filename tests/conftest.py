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
    settings.ENABLE_EXT_PROC = False
    settings.AGENT_IDENTITY_ENFORCER = "off"
    settings.EXT_PROC_SOCK_PATH = "/tmp/ext_proc.sock"
    settings._valid_virtual_keys_set = frozenset()
    settings.VALID_VIRTUAL_KEYS = ""
    settings.SHIELD_ENCRYPTION_KEY = "00" * 32

    # Most of the existing suite exercises BYOK passthrough directly with
    # provider-shaped keys (sk-proj-/sk-ant-/AIza) and predates the opt-in gate on
    # that path (ENABLE_OPEN_BYOK_PASSTHROUGH, default False in production). Tests
    # that specifically verify the gate itself override this back to False locally.
    settings.ENABLE_OPEN_BYOK_PASSTHROUGH = True

    # Ensure telemetry endpoint is None during tests to prevent httpx_mock AssertionError
    # on unexpected background POST requests. ANONYMOUS_USAGE_TRACKING remains True
    # to ensure the proxy behaves correctly and swallows errors as expected.
    settings.TELEMETRY_ENDPOINT_URL = None

    from llm_shield_proxy.security.circuit_breaker import circuit_breaker_cache

    circuit_breaker_cache.clear()

    from llm_shield_proxy.security.identity import _dpop_replay_cache

    _dpop_replay_cache.clear()

    if hasattr(app.state, "http_client"):
        app.state.http_client = None

    from llm_shield_proxy.api.main import app_state

    app_state.is_draining = False

    from llm_shield_proxy.api.health import _readyz_cache

    _readyz_cache.clear()
