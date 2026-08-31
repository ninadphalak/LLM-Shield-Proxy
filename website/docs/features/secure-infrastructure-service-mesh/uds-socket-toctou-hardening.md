# UDS Socket TOCTOU Hardening

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
**UDS Socket TOCTOU (Time-of-Check to Time-of-Use) Hardening** is a highly specific, OS-level security feature. When running the proxy in Service Mesh mode using a Unix Domain Socket (UDS) instead of a TCP port, this feature prevents local privilege escalation attacks by securely managing socket file permissions at the kernel level.

## How It Works
A common vulnerability when creating Unix Domain Sockets in Linux is the TOCTOU race condition. If an application creates a socket at `/var/run/shield.sock` and *then* calls `chmod` to restrict access, there is a split-second window where a malicious local process can connect to the socket before the permissions are tightened.

1. **Pre-Emptive Umask:** Before the proxy instructs the OS to create the UDS file, it explicitly executes `os.umask(0o117)`.
2. **Atomic Creation:** The kernel uses the umask to atomically create the socket file with strict `rw-rw----` (660) permissions natively.
3. **Reduced permission window:** Applying a restrictive process umask before socket creation avoids the specific create-then-chmod window. It does not address compromised peer containers, shared UID/GID choices, host administrators, or every local IPC threat.
4. **Context Restoration:** The proxy immediately restores the original umask so that subsequent file operations (like audit logging) behave normally.


```mermaid
flowchart TD
    A[Start Proxy UDS Server] --> B(os.umask 0o117)
    B --> C(Bind /var/run/shield.sock)
    C -->|Atomically Secure| D[Restore Original umask]
    D --> E[Accept gRPC Traffic]
```


View diagram on GitHub mobile 📱 -->


## Performance Profile
- **Performance:** Workload and environment dependent; measure this path under the published benchmark protocol.
- **Overhead:** Zero.

## Configuration Flags

| Environment Variable | Description | Linked Deployment Guide |
| :--- | :--- | :--- |
| `EXT_PROC_SOCK_PATH` | The Unix Domain Socket path (default `/var/run/llm-shield/ext_proc.sock`). | [View in deployment.md](/docs/deployment) |

## Critical Logic & Edge Cases
* **File Cleanup:** Handled shutdown paths unlink the socket. Abrupt termination can leave a stale path, so startup must also handle stale sockets safely.
* **Container context:** This protection is most relevant on a shared volume such as `emptyDir`. UID/GID, mount permissions, Linux security controls, and host administration determine which peers can access the socket.

## FAQ

**Q: Do I need to worry about this if I'm just running the proxy on port 8000?**
A: No. This specific hardening technique only applies when you are using the [Service Mesh Native gRPC ext_proc Integration](./service-mesh-native-grpc-ext-proc-integration) via Unix Domain Sockets, as TCP ports do not suffer from file-level permission race conditions.


## Plainspeak
This feature closes a tiny, split-second window of vulnerability when the proxy turns on.

Applying a restrictive umask before creating the Unix socket avoids a create-then-chmod permission window. It is one local hardening measure rather than a complete defense against a compromised host or peer container.

## Related Tests
See the following test file for reference implementations and edge-case testing: [`tests/test_security_hardening.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_security_hardening.py).
