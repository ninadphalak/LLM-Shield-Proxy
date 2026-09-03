import json

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_dns(monkeypatch):
    async def mock_resolve(hostname):
        return "10.0.0.5" # private IP
    monkeypatch.setattr("llm_shield_proxy.api.main._resolve_internal_hostname", mock_resolve)

@pytest.fixture
def override_settings():
    original_air_gapped = settings.AIR_GAPPED_MODE
    original_gateway = settings.EGRESS_GATEWAY_URL
    original_forward = settings.FORWARD_CLIENT_AUTH

    settings.AIR_GAPPED_MODE = True
    settings.EGRESS_GATEWAY_URL = "http://mock-gateway:8080"
    settings.FORWARD_CLIENT_AUTH = False

    yield

    settings.AIR_GAPPED_MODE = original_air_gapped
    settings.EGRESS_GATEWAY_URL = original_gateway
    settings.FORWARD_CLIENT_AUTH = original_forward

def test_air_gapped_mode_routes_to_gateway(override_settings, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.0.5:8080/v1/chat/completions",
        json={
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello"}}
            ],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "test prompt"}]},
    )

    assert response.status_code == 200

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]

    # Assert auth header was stripped since FORWARD_CLIENT_AUTH=False
    assert "authorization" not in req.headers
    assert "x-api-key" not in req.headers

    # Assert the URL matches the resolved egress gateway but Host header is preserved
    assert str(req.url) == "http://10.0.0.5:8080/v1/chat/completions"
    assert req.headers.get("host") == "mock-gateway:8080"
    # And the original gateway hostname is carried through for TLS SNI/cert
    # verification, even though the socket connects to the pinned IP.
    assert req.extensions.get("sni_hostname") == "mock-gateway"

def test_air_gapped_mode_forwards_auth(override_settings, httpx_mock):
    settings.FORWARD_CLIENT_AUTH = True

    httpx_mock.add_response(
        method="POST",
        url="http://10.0.0.5:8080/v1/chat/completions",
        json={
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello"}}
            ],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "test prompt"}]},
    )

    assert response.status_code == 200

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]

    # Assert auth header was preserved since FORWARD_CLIENT_AUTH=True
    assert req.headers.get("authorization") == "Bearer sk-proj-mock-key"

def test_air_gapped_mode_streaming_de_redaction(override_settings, monkeypatch, httpx_mock):
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", False)
    # EMAIL, not PERSON: Tier 3 is model-backed only, so no PERSON placeholder is minted
    # without a model. What this test covers -- rehydration across the gateway hop -- is
    # unchanged.
    test_prompt = "My email is john.doe@example.com, and my phone number is 555-0199."

    # Mock chunked streaming response coming from the air-gapped egress gateway
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.0.5:8080/v1/chat/completions",
        content=(
            b'data: {"choices":[{"delta":{"content":"Welcome "}}]}\n'
            b'data: {"choices":[{"delta":{"content":"[EMA"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"IL_1]! Your phone is [PHO"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"NE_1]."}}]}\n'
            b"data: [DONE]\n"
        ),
        headers={"content-type": "text/event-stream"},
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": test_prompt}]},
    )

    assert response.status_code == 200

    # Verify the host header was preserved for TLS SNI / Host routing
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers.get("host") == "mock-gateway:8080"

    text = response.text
    content = ""
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            data = json.loads(line[6:])
            content += data["choices"][0]["delta"].get("content", "")

    # Assert proper rehydration occurred despite gateway hop
    assert "john.doe@example.com" in content
    assert "555-0199" in content
    assert "[EMAIL_1]" not in content
    assert "[PHONE_1]" not in content
