import pytest
from llm_shield_proxy.api.main import app

@pytest.fixture(autouse=True)
def clear_http_client():
    if hasattr(app.state, "http_client"):
        app.state.http_client = None
