"""Destination routing for Claude models that are not served by Anthropic.

Claude is resold under a namespaced model ID by OpenRouter, Vertex AI, Bedrock and
any Azure deployment named after it. The provider adapter used to infer its
destination from the model string, so every one of those requests was retargeted
to ``api.anthropic.com`` -- discarding ``UPSTREAM_BASE_URL``, bypassing the
air-gapped egress gateway, and forwarding the credential that had been injected
for the *configured* upstream to Anthropic instead.

These tests assert on the URL the proxy actually dials, which is the only thing
that distinguishes the bug from the fix.

Every test takes ``httpx_mock``, including the ones that expect a rejection. A
test that expects a 401 and registers no upstream would otherwise make a real
network call and pass on the upstream's own 401 rather than on the proxy's -- the
failure mode that hid the true behaviour of the BYOK gate while it was being
diagnosed here.
"""

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)

OPENAI_RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
}

ANTHROPIC_RESPONSE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hello"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 2},
}


@pytest.fixture
def upstream(monkeypatch):
    """Points the proxy at a given upstream for one test."""

    def _set(base_url):
        monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", base_url, raising=False)

    return _set


@pytest.mark.parametrize(
    "base_url,model,expected_url",
    [
        pytest.param(
            "https://openrouter.ai/api",
            "anthropic/claude-sonnet-4.5",
            "https://openrouter.ai/api/v1/chat/completions",
            id="openrouter",
        ),
        pytest.param(
            "https://us-east5-aiplatform.googleapis.com",
            "claude-sonnet-4-5@20250929",
            "https://us-east5-aiplatform.googleapis.com/v1/chat/completions",
            id="vertex",
        ),
        pytest.param(
            "https://my-org.openai.azure.com",
            "my-claude-deployment",
            "https://my-org.openai.azure.com/v1/chat/completions",
            id="azure-deployment-name",
        ),
    ],
)
def test_claude_model_reaches_the_configured_upstream(upstream, httpx_mock, base_url, model, expected_url):
    upstream(base_url)
    httpx_mock.add_response(method="POST", url=expected_url, json=OPENAI_RESPONSE)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    sent = requests[0]

    assert str(sent.url) == expected_url
    assert "anthropic.com" not in str(sent.url)

    # The caller's key must stay a bearer token. Moving it into x-api-key is what
    # leaked the wrong credential to the wrong vendor.
    assert sent.headers.get("authorization") == "Bearer sk-proj-mock-key"
    assert "x-api-key" not in sent.headers

    # Payload must remain OpenAI-shaped: no Anthropic Messages translation.
    body = sent.read().decode()
    assert '"messages"' in body


def test_anthropic_upstream_still_engages_the_adapter(upstream, httpx_mock):
    """The documented contract: the adapter engages on an Anthropic target URL."""
    upstream("https://api.anthropic.com")
    httpx_mock.add_response(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        json=ANTHROPIC_RESPONSE,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-ant-mock-key"},
        json={
            "model": "claude-3-opus-20240229",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    sent = requests[0]

    # Path is swapped to /v1/messages, and the credential moves to x-api-key.
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers.get("x-api-key") == "sk-ant-mock-key"
    assert "authorization" not in sent.headers

    # Response is normalized back to the OpenAI shape for the caller.
    assert response.json()["choices"][0]["message"]["content"] == "Hello"


def test_anthropic_target_url_is_derived_not_hardcoded(upstream, httpx_mock):
    """A self-hosted Anthropic-compatible endpoint keeps its own host and path prefix.

    The old hardcoded ``https://api.anthropic.com/v1/messages`` sent this request
    to the public API instead, which in air-gapped deployments meant egressing
    straight past the gateway that exists to prevent exactly that.
    """
    upstream("https://anthropic-mirror.internal.example/proxy")
    httpx_mock.add_response(
        method="POST",
        url="https://anthropic-mirror.internal.example/proxy/v1/chat/completions",
        json=OPENAI_RESPONSE,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "claude-3-opus-20240229", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    sent = httpx_mock.get_requests()[0]
    assert "anthropic.com" not in str(sent.url)
    assert str(sent.url).startswith("https://anthropic-mirror.internal.example/proxy/")


def test_explicit_provider_header_still_overrides(upstream, httpx_mock):
    """X-Shield-Provider is an instruction; it translates but does not retarget."""
    upstream("https://api.anthropic.com")
    httpx_mock.add_response(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        json=ANTHROPIC_RESPONSE,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-ant-mock-key", "X-Shield-Provider": "anthropic"},
        json={
            "model": "some-non-claude-alias",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert str(httpx_mock.get_requests()[0].url) == "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# BYOK key-shape gate (ENABLE_OPEN_BYOK_PASSTHROUGH / BYOK_KEY_PREFIXES)
# ---------------------------------------------------------------------------


def test_openrouter_shaped_key_is_accepted_as_byok(upstream, httpx_mock):
    """An OpenRouter key is BYOK-shaped, so the passthrough gate admits it.

    The accepted prefixes used to be a literal tuple in api/main.py, so every new
    upstream a deployment fronted needed a source change and a release. They now
    come from settings.byok_key_prefixes.
    """
    upstream("https://openrouter.ai/api")
    httpx_mock.add_response(
        method="POST",
        url="https://openrouter.ai/api/v1/chat/completions",
        json=OPENAI_RESPONSE,
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-or-v1-mock-key"},
        json={"model": "anthropic/claude-sonnet-4.5", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200

    sent = httpx_mock.get_requests()[0]
    assert str(sent.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert sent.headers.get("authorization") == "Bearer sk-or-v1-mock-key"


def test_byok_prefixes_can_be_narrowed(upstream, httpx_mock, monkeypatch):
    """Overriding REPLACES the built-in list, so a deployment can shrink the surface.

    No upstream is registered on purpose: the rejection must happen at the proxy's
    own gate, before anything is dialed.
    """
    upstream("https://openrouter.ai/api")
    monkeypatch.setattr(settings, "BYOK_KEY_PREFIXES", "sk-ant-", raising=False)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-or-v1-mock-key"},
        json={"model": "anthropic/claude-sonnet-4.5", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert httpx_mock.get_requests() == []


def test_blank_byok_prefix_entries_do_not_become_allow_all(upstream, httpx_mock, monkeypatch):
    """A stray comma must not admit every key.

    ``"anything".startswith("")`` is True, so an empty entry surviving the split
    would silently turn an allowlist into allow-all -- the classic fail-open.
    """
    upstream("https://openrouter.ai/api")
    monkeypatch.setattr(settings, "BYOK_KEY_PREFIXES", "sk-ant-, ,", raising=False)
    assert settings.byok_key_prefixes == ("sk-ant-",)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer totally-not-a-provider-key"},
        json={"model": "anthropic/claude-sonnet-4.5", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert httpx_mock.get_requests() == []


def test_byok_prefixes_that_parse_to_nothing_deny_rather_than_admit(monkeypatch):
    """An override of only blanks is malformed config; it must fail closed."""
    monkeypatch.setattr(settings, "BYOK_KEY_PREFIXES", " , , ", raising=False)
    assert settings.byok_key_prefixes == ()
    # str.startswith(()) is False for every input, so the gate denies.
    assert not "sk-proj-anything".startswith(settings.byok_key_prefixes)


def test_byok_prefixes_default_to_the_builtin_list(monkeypatch):
    from llm_shield_proxy.core.config import DEFAULT_BYOK_KEY_PREFIXES

    monkeypatch.setattr(settings, "BYOK_KEY_PREFIXES", "", raising=False)
    assert settings.byok_key_prefixes == DEFAULT_BYOK_KEY_PREFIXES
    assert "sk-or-v1-" in settings.byok_key_prefixes
