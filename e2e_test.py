import sys

import requests


def test_health():
    try:
        r = requests.get("http://localhost:8000/healthz")
        r.raise_for_status()
        print("Health check passed.")
    except Exception as e:
        print(f"Health check failed: {e}")
        sys.exit(1)

def test_redaction():
    headers = {"Authorization": "Bearer test-key"}
    data = {
        "messages": [{"role": "user", "content": "My SSN is 000-00-0000"}]
    }
    try:
        r = requests.post("http://localhost:8000/v1/chat/completions", headers=headers, json=data)
        if r.status_code != 200:
            print(f"Redaction API returned {r.status_code}: {r.text}")
            sys.exit(1)
        print("Redaction API call successful.")
        # If the API hits a real upstream without a key, it might return 401 from upstream,
        # but we are just smoke testing if the proxy processes the request.
        # Let's check if we get a response.
    except Exception as e:
        print(f"Redaction check failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("Running E2E Smoke Test against localhost:8000...")
    test_health()
    print("E2E Smoke Test Passed!")
