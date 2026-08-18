import copy
import logging

logger = logging.getLogger(__name__)

class AnthropicAdapter:
    MODEL_ALIASES = {
        "gpt-4o": "claude-3-5-sonnet-20241022",
        "gpt-4-turbo": "claude-3-opus-20240229",
        "gpt-3.5-turbo": "claude-3-haiku-20240307",
    }

    @staticmethod
    def transform_request(openai_payload: dict) -> dict:
        """
        Translates OpenAI JSON schema to Anthropic Messages API schema.
        - Deep copies to avoid mutating proxy state.
        - Extracts 'system' messages and concatenates them to top-level.
        - Joins consecutive messages of the same role.
        - Enforces strictly alternating roles starting with 'user'.
        """
        payload = copy.deepcopy(openai_payload)
        anthropic_payload = {}
        
        # Model mapping
        original_model = payload.get("model", "gpt-4o")
        anthropic_payload["model"] = AnthropicAdapter.MODEL_ALIASES.get(original_model, original_model)
        
        # Top-level settings
        anthropic_payload["max_tokens"] = payload.get("max_tokens", 4096)
        if "temperature" in payload:
            anthropic_payload["temperature"] = payload["temperature"]
        if "top_p" in payload:
            anthropic_payload["top_p"] = payload["top_p"]
        if "stream" in payload:
            anthropic_payload["stream"] = payload["stream"]
            
        system_prompts = []
        messages = payload.get("messages", [])
        filtered_messages = []
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_prompts.append(content)
            else:
                filtered_messages.append({"role": role, "content": content})
                
        if system_prompts:
            anthropic_payload["system"] = "\n\n".join(system_prompts)
            
        # Collapse consecutive roles and enforce strictly alternating
        final_messages = []
        for msg in filtered_messages:
            role = msg["role"]
            content = msg["content"]
            # Anthropic only supports 'user' and 'assistant'
            if role not in ("user", "assistant"):
                role = "user" # fallback for 'tool' etc. if not specifically handled
                
            if not final_messages:
                if role == "assistant":
                    # Must start with user
                    final_messages.append({"role": "user", "content": "Hello"})
                final_messages.append({"role": role, "content": content})
            else:
                if final_messages[-1]["role"] == role:
                    final_messages[-1]["content"] += "\n\n" + content
                else:
                    final_messages.append({"role": role, "content": content})
                    
        if not final_messages:
            final_messages.append({"role": "user", "content": "Hello"})
            
        anthropic_payload["messages"] = final_messages
        return anthropic_payload

    @staticmethod
    def transform_response(anthropic_payload: dict) -> dict:
        """
        Translates Anthropic Messages API JSON to OpenAI ChatCompletion schema.
        """
        content_blocks = anthropic_payload.get("content", [])
        text_content = "".join([block.get("text", "") for block in content_blocks if block.get("type") == "text"])
        
        usage = anthropic_payload.get("usage", {})
        
        openai_res = {
            "id": anthropic_payload.get("id", "chatcmpl-anthropic"),
            "object": "chat.completion",
            "created": 0,
            "model": anthropic_payload.get("model", "claude"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text_content
                    },
                    "finish_reason": "stop" if anthropic_payload.get("stop_reason") == "end_turn" else anthropic_payload.get("stop_reason")
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            }
        }
        return openai_res
