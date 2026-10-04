"""LLM-Shield-Proxy Quickstart Demo.

Demonstrates drop-in streaming chat completion with automatic PII masking and real-time rehydration.

Run `docker compose up -d` first, with OPENAI_API_KEY set in your shell. The client sends
the proxy's own key (VALID_VIRTUAL_KEYS in docker-compose.yml, `demo-key` by default), and
the proxy swaps in your OpenAI key upstream.
"""

import os

from openai import APIConnectionError, AuthenticationError, OpenAI

PROXY_URL = "http://localhost:8000/v1"

client = OpenAI(
    api_key=os.getenv("SHIELD_VIRTUAL_KEY", "demo-key"),
    base_url=PROXY_URL,
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
    print("\n\n[DONE] Streamed through the proxy.")

except APIConnectionError:
    print(f"\n[Error] Nothing is listening on {PROXY_URL}. Start the proxy with `docker compose up -d`.")
except AuthenticationError as e:
    if "Invalid Proxy API Key" in str(e):
        print(
            "\n[Error] The proxy rejected the client key. Set SHIELD_VIRTUAL_KEY to a key listed in "
            "the proxy's VALID_VIRTUAL_KEYS (docker-compose.yml uses demo-key)."
        )
    else:
        print(f"\n[Error] The model provider rejected the proxy's upstream key. Check OPENAI_API_KEY: {e}")
except Exception as e:
    print(f"\n[Error] {e}")
