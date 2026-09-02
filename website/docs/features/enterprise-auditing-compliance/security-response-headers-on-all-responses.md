# Security Response Headers

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Security Response Headers** middleware adds configured headers to responses that traverse the middleware stack. Some server, framework, proxy, and early-failure responses can follow different paths, so verify success, streaming, error, and infrastructure-generated responses.

## How It Works
The middleware sets browser security headers on responses that pass through the supported
application path.

1. **Middleware Injection:** A FastAPI middleware layer adds headers to responses that traverse that application path.
2. **Configured values:** The middleware sets the documented header values; infrastructure-generated or bypass responses require separate verification.
3. **The Headers:**
   - `X-Content-Type-Options: nosniff` asks browsers not to guess a different content type.
   - `X-Frame-Options: DENY` asks browsers not to embed the response in a frame.
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains` tells compatible browsers
     to use HTTPS for the host and its subdomains for one year after receiving the header over
     HTTPS.


```mermaid
flowchart LR
    A[Upstream LLM Response] --> B(Proxy Middleware)
    B --> C(Inject Security Headers)
    C --> D[HTTP Response with Security Headers]
    D --> E[Client Browser]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Header construction and middleware dispatch perform work; measure the complete request path under the published protocol.

## Configuration Flags
These headers provide browser hardening defaults; they do not establish OWASP compliance or replace application-specific CSP, CORS, TLS, cookie, and content-handling review.

## Critical Logic & Edge Cases
* **HSTS scope:** Browsers honor HSTS only after receiving it over HTTPS. The one-year setting can
  affect subdomains because `includeSubDomains` is enabled. Confirm that this is appropriate for
  every affected host before deployment.
* **CORS interaction:** Security and CORS headers are configured separately, but browser behavior depends on their combined values. Test the intended origins, methods, credentials, and error responses.

## FAQ

**Q: Do these headers affect Server-Sent Events (SSE)?**
A: The application middleware is intended to add them to the initial SSE response. Verify this through the selected ASGI server, ingress, error path, and TLS terminator; the headers do not encrypt transport by themselves.


## Practical effect
These response headers ask compatible browsers to disable MIME sniffing, framing, and future plaintext HTTP access for the configured host. Their effect depends on HTTPS, browser support, intermediaries, and the rest of the application's security policy.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_security_hardening.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_security_hardening.py).
