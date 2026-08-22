from llm_shield_proxy.adapters.anthropic_adapter import AnthropicAdapter
from llm_shield_proxy.adapters.provider_factory import resolve_provider


def test_resolve_provider_header():
    headers = {"x-shield-provider": "anthropic"}
    assert resolve_provider(headers, {}) == "anthropic"


def test_resolve_provider_model_claude():
    headers = {}
    payload = {"model": "claude-3-opus-20240229"}
    assert resolve_provider(headers, payload) == "anthropic"


def test_resolve_provider_model_openai():
    headers = {}
    payload = {"model": "gpt-4"}
    assert resolve_provider(headers, payload) == "openai"  # Assuming openai is default


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

