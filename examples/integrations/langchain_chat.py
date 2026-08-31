"""Minimal LangChain smoke client for an already-running LLM-Shield-Proxy."""

import os

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

chat = ChatOpenAI(
    base_url=os.getenv("SHIELD_BASE_URL", "http://localhost:8000/v1"),
    api_key=os.getenv("SHIELD_CLIENT_KEY", "sk-shield-local-evaluation"),
    model=os.getenv("SHIELD_MODEL", "gpt-4o-mini"),
    streaming=True,
)

message = HumanMessage(content="Synthetic test record: account 000-11-2222")
for chunk in chat.stream([message]):
    print(chunk.content or "", end="", flush=True)
