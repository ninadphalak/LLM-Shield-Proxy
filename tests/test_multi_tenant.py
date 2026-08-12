import os
from openai import OpenAI

# 1. Test routing to OpenAI using X-Upstream-Base-Url
# Since the proxy has OPENAI_API_KEY (if we set it) or since we don't, we will pass a dummy and it will fail with 401 if it hits OpenAI correctly.
client = OpenAI(
    api_key="sk-local-test-key", 
    base_url="http://localhost:8000/v1",
    default_headers={"X-Upstream-Base-Url": "https://api.openai.com"}
)
try:
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print("OpenAI Success:", response)
except Exception as e:
    print("OpenAI Result:", e)


# 2. Test routing to Gemini using X-Upstream-Base-Url
client2 = OpenAI(
    api_key="sk-local-test-key", 
    base_url="http://localhost:8000/v1",
    default_headers={"X-Upstream-Base-Url": "https://generativelanguage.googleapis.com/v1beta/openai/"}
)
try:
    response = client2.chat.completions.create(
        model="gemini-3.5-flash",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print("Gemini Success:", response.choices[0].message.content)
except Exception as e:
    print("Gemini Result:", e)
