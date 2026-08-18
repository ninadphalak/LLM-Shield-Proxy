import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    data = await request.json()
    is_stream = data.get("stream", False)

    if not is_stream:
        return {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1677652288,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "This is a mock response containing [API_KEY] and [EMAIL] synthetic tokens to test rehydration.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
        }

    async def sse_generator():
        chunks = [
            "This is ",
            "a mock ",
            "streaming ",
            "response ",
            "containing ",
            "[API_KEY] ",
            "and ",
            "[EMAIL] ",
            "synthetic ",
            "tokens.",
        ]
        for chunk in chunks:
            payload = {"choices": [{"delta": {"content": chunk}}]}
            yield f"data: {json.dumps(payload)}\n\n"
            # artificial delay to simulate token streaming
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mock_upstream:app", host="127.0.0.1", port=8001, workers=20)
