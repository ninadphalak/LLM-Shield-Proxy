import orjson as json
import asyncio
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
    client_disconnected = False
    MAX_LINE_LENGTH = 1048576  # 1MB limit to prevent Slowloris buffer poisoning

    try:
        async for chunk in raw_stream:
            chunk_text = chunk.decode("utf-8", errors="replace")
            line_accumulator += chunk_text

            if len(line_accumulator) > MAX_LINE_LENGTH:
                raise ValueError("Line accumulator exceeded maximum safe length (Slowloris protection)")

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
                                line = f"data: {json.dumps(data_obj).decode('utf-8')}"
                    except json.JSONDecodeError:
                        pass
                    
                    yield (line + "\n").encode("utf-8")
                elif stripped == "data: [DONE]":
                    # Flush the buffer BEFORE yielding the DONE signal
                    remaining = buffer.process_delta_text("", is_final=True)
                    if remaining:
                        flush_obj = {"choices": [{"delta": {"content": remaining}}]}
                        yield f"data: {json.dumps(flush_obj).decode('utf-8')}\n\n".encode("utf-8")
                    yield (line + "\n").encode("utf-8")
                else:
                    yield (line + "\n").encode("utf-8")
    except (GeneratorExit, asyncio.CancelledError):
        client_disconnected = True
        raise
    finally:
        if not client_disconnected:
            # Flush remaining buffer at stream end or stream abort
            remaining = buffer.process_delta_text("", is_final=True)
            if remaining:
                # Emit remaining flushed text if any left
                flush_obj = {
                    "choices": [{"delta": {"content": remaining}}]
                }
                yield f"data: {json.dumps(flush_obj).decode('utf-8')}\n\n".encode("utf-8")

            if line_accumulator:
                yield line_accumulator.encode("utf-8")
