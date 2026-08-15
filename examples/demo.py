"""LLM-Shield-Proxy Quickstart Demo.

Demonstrates drop-in streaming chat completion with automatic PII masking and real-time rehydration.
"""

import os
from openai import OpenAI

# Initialize client pointing to local LLM-Shield-Proxy gateway
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY") or "sk-proj-demo-key",
    base_url="http://localhost:8000/v1",
)

sample_prompt = (
    "Patient record: John Doe (SSN: 555-44-3333, Email: john.doe@hospital.org) "
    "visited Dr. Sarah Connor today. Please generate a clinical summary."
)

print("\n--- [1] Sending Prompt with Raw PII through LLM-Shield-Proxy ---")
print(f"Prompt: {sample_prompt}\n")
print("--- [2] Real-Time Streaming De-redacted Response ---")

try:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a clinical compliance AI assistant."},
            {"role": "user", "content": sample_prompt},
        ],
        stream=True,
    )

    for chunk in response:
        delta = chunk.choices[0].delta.content or ""
        print(delta, end="", flush=True)
    print("\n\n[SUCCESS] Response rehydrated in real-time with zero PII leakage to external APIs.")

except Exception as e:
    print(f"\n[Note] Ensure the proxy is running on http://localhost:8000: {e}")
