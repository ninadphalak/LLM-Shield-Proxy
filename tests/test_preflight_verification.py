import json
import os

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.config import settings
from llm_shield_proxy.main import app

try:
    import psutil
except ImportError:
    psutil = None


@pytest.fixture(autouse=True)
def setup_teardown():
    orig_swapping = settings.ENABLE_SYNTHETIC_SWAPPING
    settings.valid_virtual_keys_set = frozenset(["sk-proxy-team-a", "sk-proxy-team-b"])
    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = True
    settings.UPSTREAM_BASE_URL = "https://api.openai.com"
    settings.OPENAI_API_KEY = "mock_key"
    settings.ENABLE_SYNTHETIC_SWAPPING = False
    yield
    settings.valid_virtual_keys_set = frozenset()
    settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE = False
    settings.OPENAI_API_KEY = None
    settings.ENABLE_SYNTHETIC_SWAPPING = orig_swapping


def test_health_and_metrics():
    with TestClient(app) as client:
        # Health endpoint
        res_health = client.get("/health")
        assert res_health.status_code == 200, f"Expected 200, got {res_health.status_code}"
        assert res_health.json()["status"] == "ok"

        # Metrics endpoint
        res_metrics = client.get("/metrics")
        assert res_metrics.status_code == 200


def test_tenant_isolation_and_ssrf(httpx_mock):
    with TestClient(app) as client:
        # 1. SSRF check
        res_ssrf = client.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-a", "x-upstream-base-url": "http://169.254.169.254"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert res_ssrf.status_code == 403, f"Expected 403 for SSRF, got {res_ssrf.status_code}"

        # 2. Tenant isolation
        # Team A sends PII
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "Received [PERSON_1]"}}]},
        )
        res_a = client.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-a", "x-session-id": "sess_prod"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "My name is John Doe"}]},
        )
        assert res_a.status_code == 200
        assert "John Doe" in res_a.text

        # Team B tries to access Team A's vault
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "Received [PERSON_1]"}}]},
        )
        res_b = client.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-b", "x-session-id": "sess_prod"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "What is the name?"}]},
        )
        assert res_b.status_code == 200
        assert "John Doe" not in res_b.text
        assert "[PERSON_1]" in res_b.text


def test_end_to_end_streaming_rehydration(httpx_mock):
    with TestClient(app) as client:
        # Pre-populate vault by sending a non-streaming request first
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        client.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-a", "x-session-id": "sess_stream"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "My name is Alice Smith"}]},
        )

        # Now do the streaming request
        httpx_mock.add_response(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            content=(
                b'data: {"choices":[{"delta":{"content":"Contact "}}]}\n'
                b'data: {"choices":[{"delta":{"content":"[PER"}}]}\n'
                b'data: {"choices":[{"delta":{"content":"SON_1] at 555-0199."}}]}\n'
                b"data: [DONE]\n"
            ),
            headers={"content-type": "text/event-stream"},
        )
        res = client.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-a", "x-session-id": "sess_stream"},
            json={"model": "gpt-4", "stream": True, "messages": [{"role": "user", "content": "What was the contact?"}]},
        )
        assert res.status_code == 200
        text = res.text
        content = ""
        for line in text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                data = json.loads(line[6:])
                content += data["choices"][0]["delta"]["content"]

        # We should see the fully rehydrated text
        assert "Contact Alice Smith at 555-0199." in content
        assert "[PER" not in content
        assert "SON_1]" not in content


def test_memory_rss_footprint(httpx_mock):
    """Verifies that the proxy memory footprint stays tightly bounded during massive streaming."""
    if psutil is None:
        pytest.skip("psutil not available")

    import gc

    process = psutil.Process(os.getpid())

    # Create 1000 chunks
    chunks = []
    for i in range(1000):
        chunks.append(f'data: {{"choices":[{{"delta":{{"content":"chunk{i} "}}}}]}}\n'.encode())
    chunks.append(b"data: [DONE]\n")

    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/chat/completions",
        content=b"".join(chunks),
        headers={"content-type": "text/event-stream"},
    )

    gc.collect()
    mem_before = process.memory_info().rss

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-proxy-team-a"},
            json={"model": "gpt-4", "stream": True, "messages": [{"role": "user", "content": "go"}]},
        ) as response:
            assert response.status_code == 200
            for _ in response.iter_bytes():
                # Measure during stream
                mem_during = process.memory_info().rss
                assert mem_during - mem_before < 26214400, (
                    f"Active memory exceeded 25MB during stream: {mem_during - mem_before} bytes"
                )

    gc.collect()
    mem_after = process.memory_info().rss
    assert mem_after - mem_before < 26214400, (
        f"Active memory exceeded 25MB after stream: {mem_after - mem_before} bytes"
    )
