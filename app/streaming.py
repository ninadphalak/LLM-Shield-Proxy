import json
from typing import AsyncGenerator, Optional
from app.vault import Vault


class SSERehydrationBuffer:
    """
    Sliding window buffer that prevents partial tag leakage across SSE chunks.
    Holds back partial token tags (e.g. '[PER' ... 'SON_1]') until completed across deltas.
    """
    MAX_TAG_LENGTH = 64  # Maximum expected length for tokens like [PERSON_999]

    def __init__(self, vault: Vault):
        self.vault = vault
        self.content_buffer = ""

    def process_delta_text(self, delta_text: str, is_final: bool = False) -> str:
        """
        Appends incoming delta_text to buffer, checks for unclosed tag brackets '[' near the tail,
        re-hydrates the safe portion, and returns the text ready to emit.
        """
        self.content_buffer += delta_text

        if is_final or not self.content_buffer:
            res = self.vault.rehydrate(self.content_buffer)
            self.content_buffer = ""
            return res

        last_bracket_idx = self.content_buffer.rfind('[')
        if last_bracket_idx != -1:
            # Check if matching closing bracket exists after the last open bracket
            matching_close = self.content_buffer.find(']', last_bracket_idx)
            if matching_close == -1:
                # Unclosed bracket at tail. Verify length threshold.
                tail_length = len(self.content_buffer) - last_bracket_idx
                if tail_length <= self.MAX_TAG_LENGTH:
                    # Hold tail from last_bracket_idx onward
                    safe_part = self.content_buffer[:last_bracket_idx]
                    self.content_buffer = self.content_buffer[last_bracket_idx:]
                    return self.vault.rehydrate(safe_part) if safe_part else ""

        # Buffer is safe
        safe_part = self.content_buffer
        self.content_buffer = ""
        return self.vault.rehydrate(safe_part)


async def rehydrate_sse_stream(
    raw_stream: AsyncGenerator[bytes, None],
    vault: Vault
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that processes raw SSE stream bytes from upstream LLM,
    parses SSE data lines, re-hydrates content deltas through SSERehydrationBuffer,
    and yields transformed SSE bytes.
    """
    buffer = SSERehydrationBuffer(vault)
    line_accumulator = ""

    async for chunk in raw_stream:
        chunk_text = chunk.decode("utf-8", errors="replace")
        line_accumulator += chunk_text

        while "\n" in line_accumulator:
            line, line_accumulator = line_accumulator.split("\n", 1)
            stripped = line.strip()

            if stripped.startswith("data: ") and stripped != "data: [DONE]":
                raw_json = stripped[6:]
                try:
                    data_obj = json.loads(raw_json)
                    choices = data_obj.get("choices", [])
                    if choices and isinstance(choices, list):
                        delta = choices[0].get("delta", {})
                        if "content" in delta and isinstance(delta["content"], str):
                            raw_content = delta["content"]
                            rehydrated_content = buffer.process_delta_text(raw_content)
                            delta["content"] = rehydrated_content
                            data_obj["choices"][0]["delta"] = delta
                            line = f"data: {json.dumps(data_obj)}"
                except json.JSONDecodeError:
                    # If line is not valid JSON, fallback to buffer text processing
                    pass

            yield (line + "\n").encode("utf-8")

    # Flush remaining buffer at stream end
    remaining = buffer.process_delta_text("", is_final=True)
    if remaining:
        # Emit remaining flushed text if any left
        flush_obj = {
            "choices": [{"delta": {"content": remaining}}]
        }
        yield f"data: {json.dumps(flush_obj)}\n\n".encode("utf-8")

    if line_accumulator:
        yield line_accumulator.encode("utf-8")
