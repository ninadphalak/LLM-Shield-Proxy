import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI(title="Mock OpenAI Upstream Server")

@app.post("/v1/chat/completions")
async def mock_chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    prompt_text = messages[0].get("content", "") if messages else ""

    print("\n============================================================")
    print("🤖 UPSTREAM MOCK LLM RECEIVED REDACTED REQUEST:")
    print("============================================================")
    print(f"Redacted Input Payload: {prompt_text}")
    print("============================================================\n")

    async def sse_generator():
        # Stream response back in chunks with split tokens
        chunks = [
            'data: {"choices":[{"delta":{"content":"Hello [PER"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"SON_1]! I received your message about privacy.\n\n"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"I see your registered phone number is [PHO"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"NE_1]. All PII has been safely isolated."}}]}\n\n',
            'data: [DONE]\n\n'
        ]
        for chunk in chunks:
            await asyncio.sleep(0.3)  # Simulate real-time LLM streaming delay
            yield chunk.encode("utf-8")

    return StreamingResponse(sse_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
