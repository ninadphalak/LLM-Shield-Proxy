# Troubleshooting LLM-Shield-Proxy

This document provides solutions to common issues you might encounter while developing, testing, or running LLM-Shield-Proxy.

## The Fail-Closed Security Posture

LLM-Shield-Proxy is designed for enterprise environments handling highly sensitive data (e.g., HIPAA, SOC 2, DoD workloads). Because of this, it fundamentally operates on a **"Fail-Closed"** principle. 

**What does this mean?**
If any sub-component of the proxy fails—such as the Redis vault disconnecting, the OpenTelemetry endpoint becoming unreachable, or a PII inspection engine crashing—the proxy will immediately block traffic and return an error (like a `503 Service Unavailable`). 

**Why is this important?**
In a production environment, if the DLP engine crashes and the proxy were to "Fail Open," sensitive PII would instantly leak to external third-party LLMs unredacted. Failing closed guarantees that **zero data leakage** occurs during a system failure.

## Testing and Local Proof-of-Concept (POC) Configuration

While Fail-Closed is critical for production, it can cause "developer pain" when running locally or executing test suites without a full infrastructure setup.

To seamlessly run the proxy locally, you can safely switch to a "Fail-Open" posture.

### 1. Disabling Telemetry Assertion Errors in Tests

**Symptom:**
When running the `pytest` suite, you might encounter failures with `AssertionError` messages that look like this:
```
AssertionError: The following requests were not expected:
- POST request on https://your-telemetry-endpoint.example.com/rest/v1/telemetry_logs
```

**Resolution:**
The proxy spins up background threads to dispatch telemetry logs. During testing, strict HTTP mocks (like `pytest_httpx`) will intercept and block these network calls. To resolve this for local testing, disable the telemetry endpoint by setting it to `None`:

```python
# In tests/conftest.py
@pytest.fixture(autouse=True)
def test_environment_setup():
    settings.TELEMETRY_ENDPOINT_URL = None
```

### 2. Bypassing Redis and Engine Crashes (Fail-Open)

If you are running a local POC and don't want to spin up a Redis cluster, or if you want to ensure the proxy continues routing traffic even if the DLP inspection engine throws an exception, you can explicitly configure the proxy to Fail Open.

**Resolution:**
Set the following environment variable in your `.env` or Docker configuration:

```env
SHIELD_FAILURE_MODE="FAIL_OPEN"
```

When this flag is active, any catastrophic failure in the redaction engine or state vault will be logged, but the raw payload will be allowed to egress to the upstream LLM. **Do not use this in production.**
