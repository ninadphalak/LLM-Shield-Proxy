"""Minimal LlamaIndex smoke client for an already-running LLM-Shield-Proxy."""

import os

from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base=os.getenv("SHIELD_BASE_URL", "http://localhost:8000/v1"),
    api_key=os.getenv("SHIELD_CLIENT_KEY", "sk-shield-local-evaluation"),
    model=os.getenv("SHIELD_MODEL", "gpt-4o-mini"),
)

response = llm.chat([ChatMessage(role="user", content="Synthetic test record: account 000-11-2222")])
print(response.message.content or "")
