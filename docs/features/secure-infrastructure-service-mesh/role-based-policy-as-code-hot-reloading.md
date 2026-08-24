# Role-Based Policy-as-Code & Hot-Reloading

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
**Role-Based Policy-as-Code & Hot-Reloading** allows security teams to manage Data Loss Prevention (DLP) rules declaratively via GitOps. Furthermore, it allows the proxy to dynamically ingest policy updates on-the-fly without requiring a pod restart or dropping active user connections.

## How It Works
Security policies in large organizations change rapidly. Waiting 10 minutes for a Kubernetes deployment to roll out a new regex rule is unacceptable during an active incident.

1. **GitOps Integration:** Policies are defined in a `policies.yaml` file (mounted via Kubernetes ConfigMap or pulled from an external Git repository).
2. **Asynchronous Polling:** A background `asyncio` task polls the file or network endpoint for a new SHA-256 hash.
3. **Atomic Hot-Reload:** When a change is detected, the proxy parses the new YAML into Pydantic models. It then performs an atomic, thread-safe memory pointer swap (`O(1)` complexity).
4. **Seamless Transition:** Request #100 uses the old policy. Request #101 instantly uses the new policy. No connections are dropped, and the event loop is never blocked.

<!-- EDIT THIS MERMAID SCRIPT TO UPDATE THE DIAGRAM:
```mermaid
flowchart LR
    A[Security Team pushes YAML] --> B(K8s ConfigMap Updates)
    B --> C(Proxy Polling Thread)
    C -->|Diff Detected| D[Parse & Validate]
    D --> E[Atomic Pointer Swap in Memory]
```
-->

View diagram on GitHub mobile 📱 -->
![Policy Hot-Reload Architecture](../images/role-based-policy-as-code-hot-reloading.svg)

## Performance Profile
- **Execution Speed:** File checking uses zero-cost `stat` calls. The memory swap takes `<0.1µs`.
- **Overhead:** Validating the Pydantic models takes a few milliseconds but runs concurrently on a separate worker thread to protect the main proxy loop.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `POLICY_HOT_RELOAD_INTERVAL` | The frequency in seconds to check for policy updates (default 10s). | [View in DEPLOYMENT.md](../../DEPLOYMENT.md) |

## Critical Logic & Edge Cases
* **Validation Safety:** If a security engineer pushes a malformed YAML file (e.g., missing a required field or containing a syntax error), the Pydantic validator will reject it. The proxy will log a high-priority error and *continue using the last known good policy*, preventing an accidental DoS.
* **In-Flight Streams:** If a policy is swapped while a user is in the middle of a 2-minute long streaming response, the specific request is bound to the policy state that existed when the request started, preventing mid-stream logic corruption.

## FAQ

**Q: Can I use this to hot-reload my TLS certificates?**
A: No. TLS certificates and core `google-re2` compilations (BYOR) operate at a lower C++ level and currently require a graceful pod restart to take effect. Hot-reloading strictly applies to the RBAC roles and routing settings in `policies.yaml`.


## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_policy_engine.py`](../../../tests/test_policy_engine.py).
