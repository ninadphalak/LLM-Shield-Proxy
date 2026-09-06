import pytest

from llm_shield_proxy.adapters.anthropic_adapter import AnthropicAdapter
from llm_shield_proxy.adapters.provider_factory import is_anthropic_host, resolve_provider

ANTHROPIC = "api.anthropic.com"


def test_resolve_provider_header():
    headers = {"x-shield-provider": "anthropic"}
    assert resolve_provider(headers, {}) == "anthropic"


def test_resolve_provider_header_wins_over_destination():
    """An explicit override is an instruction, not a guess, so the host cannot veto it."""
    headers = {"x-shield-provider": "anthropic"}
    payload = {"model": "gpt-4"}
    assert resolve_provider(headers, payload, "api.openai.com") == "anthropic"


def test_resolve_provider_model_claude_on_anthropic_host():
    """The documented path: Claude model, Anthropic upstream, adapter engages."""
    payload = {"model": "claude-3-opus-20240229"}
    assert resolve_provider({}, payload, ANTHROPIC) == "anthropic"


def test_resolve_provider_model_openai():
    headers = {}
    payload = {"model": "gpt-4"}
    assert resolve_provider(headers, payload) == "openai"  # Assuming openai is default


# Every one of these is a Claude model that is NOT served by Anthropic's own API.
# Resolving them to "anthropic" retargeted the request to api.anthropic.com and
# handed it the configured upstream's credential.
CLAUDE_RESOLD_ELSEWHERE = [
    pytest.param("openrouter.ai", "anthropic/claude-sonnet-4.5", id="openrouter"),
    pytest.param("us-east5-aiplatform.googleapis.com", "claude-sonnet-4-5@20250929", id="vertex"),
    pytest.param("my-org.openai.azure.com", "my-claude-deployment", id="azure-deployment-name"),
    pytest.param("bedrock-runtime.us-east-1.amazonaws.com", "anthropic.claude-3-5-sonnet-20241022-v2:0", id="bedrock"),
    pytest.param("litellm.internal", "claude-3-5-haiku", id="self-hosted-gateway"),
]


@pytest.mark.parametrize("host,model", CLAUDE_RESOLD_ELSEWHERE)
def test_claude_model_does_not_hijack_non_anthropic_upstream(host, model):
    assert resolve_provider({}, {"model": model}, host) != "anthropic"


@pytest.mark.parametrize("host,model", CLAUDE_RESOLD_ELSEWHERE)
def test_same_models_still_resolve_when_upstream_is_anthropic(host, model):
    """Proves the parametrized cases above turn on the host, not on the model string."""
    assert resolve_provider({}, {"model": model}, ANTHROPIC) == "anthropic"


def test_resolve_provider_falls_back_to_configured_upstream(monkeypatch):
    """Callers that omit upstream_host get the configured value, not a free pass."""
    from llm_shield_proxy.core import config

    monkeypatch.setattr(config.settings, "UPSTREAM_BASE_URL", "https://openrouter.ai/api", raising=False)
    assert resolve_provider({}, {"model": "anthropic/claude-sonnet-4.5"}) != "anthropic"

    monkeypatch.setattr(config.settings, "UPSTREAM_BASE_URL", "https://api.anthropic.com", raising=False)
    assert resolve_provider({}, {"model": "claude-3-opus-20240229"}) == "anthropic"


@pytest.mark.parametrize(
    "host,expected",
    [
        ("api.anthropic.com", True),
        ("anthropic.com", True),
        ("API.Anthropic.COM", True),
        ("api.anthropic.com.", True),  # trailing-dot FQDN
        ("notanthropic.com", False),  # bare endswith would accept this
        ("anthropic.com.attacker.net", False),
        ("api.anthropic.com.attacker.net", False),
        ("openrouter.ai", False),
        ("", False),
        (None, False),
    ],
)
def test_is_anthropic_host(host, expected):
    assert is_anthropic_host(host) is expected


def test_anthropic_adapter_request_transform():
    openai_payload = {
        "model": "gpt-4o",
        "max_tokens": 1000,
        "messages": [
            {"role": "system", "content": "System message 1"},
            {"role": "system", "content": "System message 2"},
            {"role": "user", "content": "User message 1"},
            {"role": "user", "content": "User message 2"},
            {"role": "assistant", "content": "Assistant message 1"},
            {"role": "assistant", "content": "Assistant message 2"},
        ],
    }

    anthropic_payload = AnthropicAdapter.transform_request(openai_payload)

    # Model alias
    assert anthropic_payload["model"] == "claude-3-5-sonnet-20241022"
    assert anthropic_payload["max_tokens"] == 1000

    # System messages concatenated
    assert anthropic_payload["system"] == "System message 1\n\nSystem message 2"

    # User messages merged, assistant messages merged
    assert len(anthropic_payload["messages"]) == 2
    assert anthropic_payload["messages"][0]["role"] == "user"
    assert anthropic_payload["messages"][0]["content"] == "User message 1\n\nUser message 2"
    assert anthropic_payload["messages"][1]["role"] == "assistant"
    assert anthropic_payload["messages"][1]["content"] == "Assistant message 1\n\nAssistant message 2"


def test_anthropic_adapter_request_transform_starts_with_assistant():
    openai_payload = {"messages": [{"role": "assistant", "content": "Should not start with assistant"}]}
    anthropic_payload = AnthropicAdapter.transform_request(openai_payload)

    assert len(anthropic_payload["messages"]) == 2
    assert anthropic_payload["messages"][0]["role"] == "user"
    assert anthropic_payload["messages"][0]["content"] == "Hello"
    assert anthropic_payload["messages"][1]["role"] == "assistant"
    assert anthropic_payload["messages"][1]["content"] == "Should not start with assistant"


def test_anthropic_adapter_response_transform():
    anthropic_res = {
        "id": "msg_01X",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "Hello world"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }

    openai_res = AnthropicAdapter.transform_response(anthropic_res)

    assert openai_res["id"] == "msg_01X"
    assert openai_res["object"] == "chat.completion"
    assert openai_res["choices"][0]["message"]["content"] == "Hello world"
    assert openai_res["choices"][0]["finish_reason"] == "stop"
    assert openai_res["usage"]["prompt_tokens"] == 10
    assert openai_res["usage"]["completion_tokens"] == 20
    assert openai_res["usage"]["total_tokens"] == 30


def test_anthropic_adapter_request_transform_stream():
    openai_payload = {"model": "gpt-4", "stream": True, "messages": [{"role": "user", "content": "Hello"}]}
    anthropic_payload = AnthropicAdapter.transform_request(openai_payload)
    assert anthropic_payload.get("stream") is True


def test_anthropic_adapter_request_transform_multipart_blocks():
    openai_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "System rule 1"},
                    {"type": "text", "text": "System rule 2"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please analyze this context."},
                ],
            },
            {
                "role": "user",
                "content": "Follow-up question.",
            },
        ],
    }
    anthropic_payload = AnthropicAdapter.transform_request(openai_payload)
    assert "System rule 1\nSystem rule 2" in anthropic_payload["system"]
    assert len(anthropic_payload["messages"]) == 1
    assert "Please analyze this context.\n\nFollow-up question." == anthropic_payload["messages"][0]["content"]

