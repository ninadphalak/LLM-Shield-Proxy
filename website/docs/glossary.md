# Plain-English glossary

Short definitions for terms used across the project. Product and compliance claims are governed by the linked technical documents, not by these simplified descriptions.

## Privacy and streaming

| Term | Plain-English meaning |
| :--- | :--- |
| **AI gateway / LLM gateway** | Software placed between an application and a model provider to inspect, change, route, or record requests and responses. |
| **Proxy / reverse proxy** | A service that receives a client's request and sends it to another service on the client's behalf. |
| **Upstream** | The model service or gateway to which this proxy sends a transformed request. |
| **Configured upstream boundary** | The request bytes the proxy sends to the selected model provider after applying its changes. |
| **In-VPC** | Running inside the operator-controlled virtual private cloud or equivalent private network. |
| **Zero egress** | In this project, the outgoing provider request does not contain the known test values. The proxy still makes a network request with the masked content. |
| **PII** | Personally identifiable information, such as an email address or government identifier. |
| **PHI / ePHI** | Health information protected by HIPAA; ePHI is the electronic form. |
| **PCI data** | Payment-card information governed by PCI DSS controls. |
| **Redaction** | Removing or replacing a protected value before forwarding content. |
| **Masking / tokenization** | Replacing a protected value with a safer representation or placeholder. |
| **Placeholder** | A temporary marker such as `[EMAIL_1]` that stands in for a protected value. |
| **Rehydration** | Replacing an authorized placeholder with its original value on the response path. |
| **SSE** | Server-Sent Events, an HTTP format that delivers model output incrementally as `data:` events. |
| **Fragmentation** | A logical value being split across multiple network, byte, or SSE chunks. |
| **Sliding-window buffer** | A small retained suffix used to decide whether the end of one chunk could be the start of a placeholder completed by a later chunk. |
| **Bounded state** | Memory retained by an algorithm has a declared upper bound instead of growing with the full stream. |

## Detection and transformation

| Term | Plain-English meaning |
| :--- | :--- |
| **Detection tier** | One stage of the local detector pipeline, such as structured patterns, entropy, or an optional entity model. |
| **Shannon entropy** | A measure of character unpredictability used here as a heuristic for secret-like strings. High entropy is not proof that a string is a secret. |
| **NER** | Named-entity recognition, a model that labels text spans such as people, organizations, or places. |
| **ONNX** | A portable model format and runtime used for the optional local NER tier. |
| **Regex** | A pattern used to find structured text such as an email or SSN. |
| **DFA** | Deterministic finite automaton, a pattern-matching method that can avoid catastrophic regex backtracking for supported patterns. |
| **ReDoS** | Regular-expression denial of service, where a pathological pattern or input consumes excessive CPU. |
| **AST** | Abstract syntax tree, a parsed representation of structured data that lets the proxy change values without editing raw JSON text blindly. |
| **JSON-RPC** | A JSON-based request and response protocol commonly used by tools and MCP servers. |
| **MCP** | Model Context Protocol, a protocol through which models or agents discover and call tools. |
| **Tool call** | A structured model request to execute a named function or external capability. |

## Access and network security

| Term | Plain-English meaning |
| :--- | :--- |
| **RBAC** | Role-based access control: permissions are assigned to roles, and identities receive roles. |
| **Policy as code** | Access and security rules stored in machine-readable configuration and evaluated by software. |
| **OPA** | Open Policy Agent, an external engine that evaluates policy rules. |
| **Fail closed** | Deny or stop when a security decision cannot be completed safely. |
| **SSRF** | Server-side request forgery, where an attacker tricks a server into reaching an unintended network address. |
| **DNS rebinding** | Changing a hostname's resolved address to bypass an earlier network check. |
| **mTLS** | Mutual TLS, where both ends of a connection authenticate with certificates. |
| **UDS** | Unix domain socket, a local operating-system communication channel that does not use a TCP network port. |
| **ASGI** | The Python interface between asynchronous web servers and applications such as FastAPI. |

## Cryptography and audit evidence

| Term | Plain-English meaning |
| :--- | :--- |
| **AES-256-GCM** | Authenticated encryption that hides data and detects ciphertext modification. |
| **DEK** | Data-encryption key used to encrypt a particular value or data set. |
| **HKDF** | A standard method for deriving separate cryptographic keys from shared key material. |
| **HMAC** | A keyed hash used to authenticate data when verifier and signer share a secret. |
| **SHA-256** | A cryptographic hash that produces a fixed-size fingerprint of data. |
| **Ed25519** | A public-key signature algorithm. A private key signs; the public key verifies. |
| **Hash chain** | Records linked by including the previous record's hash in the next record. |
| **Tamper-evident** | Modification can be detected during verification. It does not mean modification is impossible. |
| **WORM** | Write once, read many storage that prevents protected objects from being changed or deleted during their retention period. |
| **Immutable retention** | A storage policy that prevents overwrite or deletion for a defined period or legal hold. |
| **Checkpoint** | A signed summary of one or more chains' terminal hashes and sequence numbers at a point in time. |
| **Anchoring** | Keeping a checkpoint in a separate trust domain so later truncation or replacement can be detected. |
| **Chain ID** | Identifier for one independently ordered audit chain. |
| **Sequence number** | Increasing number used to detect missing or reordered records in a chain. |
| **Key custody** | The processes and systems that control who can use, rotate, revoke, and archive a private key. |
| **Key fingerprint** | A short hash-derived identifier used to distinguish one public key from another. |
| **`fsync`** | An operating-system request to flush file data toward durable storage; it is not a WORM guarantee. |
| **Best-effort audit** | Audit delivery that avoids blocking the request path and may drop records under pressure. |
| **Required audit** | Audit delivery that surfaces persistence failure instead of silently continuing. |

## Assurance, compliance, and measurement

| Term | Plain-English meaning |
| :--- | :--- |
| **Conformance specification** | A versioned list of behaviors and report fields an implementation must test. |
| **Test vector** | A declared input and expected outcome used to check one behavior. |
| **Negative control** | A deliberately damaged or failing input used to prove that a test can detect failure. |
| **OSCAL** | NIST's machine-readable format for exchanging security-control and assessment information. It does not itself prove compliance. |
| **NIST SP 800-53** | A catalog of security and privacy controls used by many government and enterprise programs. |
| **SOC 2** | An independent examination of controls relevant to selected Trust Services Criteria over a defined scope and period. |
| **HIPAA** | A US health-information law and rule set whose obligations apply to covered entities, business associates, and their safeguards. |
| **Audit evidence** | Records and artifacts used to test whether a control was designed and operated as claimed. |
| **OpenTelemetry / OTel** | A standard way to generate and export traces, metrics, and logs. |
| **p50 / p95 / p99** | Latency percentiles. p95 means 95 percent of measured samples were at or below that value. |
| **RSS** | Resident set size, an operating-system measurement of process memory currently held in RAM. |
| **In-process benchmark** | Timing of selected code inside one process, excluding the complete network service path. |
| **Confidence interval** | A range expressing uncertainty around an estimate from repeated samples. |
