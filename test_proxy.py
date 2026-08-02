import os
import sys
from openai import OpenAI

# Initialize standard OpenAI client pointed to local LLM-Shield-Proxy
proxy_url = os.getenv("PROXY_URL", "http://localhost:8000/v1")
api_key = os.getenv("OPENAI_API_KEY", "sk-mock-key-for-local-testing")

client = OpenAI(
    base_url=proxy_url,
    api_key=api_key
)

# Test prompt containing PII (Person Name: John Doe, Phone Number: 555-0199)
test_prompt = "My name is John Doe, and my phone number is 555-0199. What can you tell me about data privacy?"

print(f"============================================================")
print(f"🛡️  LLM-Shield-Proxy Real-Time Proxy Stream Test")
print(f"============================================================")
print(f"Target Proxy URL : {proxy_url}")
print(f"Original Prompt  : {test_prompt}")
print(f"============================================================")
print(f"Streaming Response (Watching Real-Time Re-hydration):\n")

try:
    model_name = os.getenv("MODEL_NAME", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "user", "content": test_prompt}
        ],
        stream=True
    )

    for chunk in response:
        if chunk.choices and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            content = delta.content or ""
            sys.stdout.write(content)
            sys.stdout.flush()

    print("\n\n============================================================")
    print("✅ Stream finished cleanly.")
except Exception as e:
    print(f"\n❌ Error connecting to proxy: {e}")
    print("Ensure LLM-Shield-Proxy is running on http://localhost:8000 or via Docker.")
