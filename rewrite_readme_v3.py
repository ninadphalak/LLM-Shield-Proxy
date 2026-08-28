import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Remove the old Redaction Modes section entirely
# It spans from "## 🛡️ Redaction Modes" to the next "---"
old_section_pattern = r"## 🛡️ Redaction Modes.*?---"
text = re.sub(old_section_pattern, "---", text, flags=re.DOTALL)

# 2. Define the new Dual-Pipeline architecture section
new_section = """## 🛡️ Dual-Pipeline Redaction Architecture

LLM-Shield-Proxy intelligently routes traffic through two distinct redaction pipelines based on the payload structure. This ensures that autonomous agents don't crash from broken syntax trees, while human prompts get the highest quality contextual masking.

```mermaid
flowchart TD
    classDef stateful fill:#fffbeb,stroke:#f59e0b,stroke-width:2px,color:#92400e
    classDef stateless fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a
    classDef router fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#0f172a

    Client[Client App] --> Router{JSON-RPC / Tool Call?}:::router
    
    %% Path A: Human Traffic
    Router -->|No: Standard Text| PathA[A. Human-to-LLM Prompt]
    PathA --> Syn[SYNTHETIC]:::stateful
    PathA --> Tag[STRUCTURAL_TAG]:::stateful
    PathA --> Scrub[SCRUB]:::stateless
    PathA --> CryptoA[STATELESS_CRYPTO]:::stateless
    
    %% Path B: Machine Traffic
    Router -->|Yes: Structured Code| PathB[B. Machine-to-Machine]
    PathB --> CryptoB[Strict STATELESS_CRYPTO]:::stateless
    
    Syn -.-> Redis[(Redis Vault)]:::stateful
    Tag -.-> Redis
    
    CryptoA -.-> AES[In-Band AES-256-GCM]:::stateless
    CryptoB -.-> AES
```

### A. Human-to-LLM (Text Prompts)
For standard conversational text, the proxy respects your configured masking mode. You can choose from four strategies:
1. **SYNTHETIC (Stateful):** Swaps PII with canonical locale fakes (e.g., `John` -> `Maya`). Preserves LLM attention weights and token counts. Requires Redis.
2. **STRUCTURAL_TAG (Stateful):** Swaps PII with explicit bracketed tags (e.g., `[PERSON_1]`). Requires Redis.
3. **SCRUB (Stateless):** Destructive one-way redaction (`***`). Cannot be rehydrated.
4. **STATELESS_CRYPTO (Stateless):** Encrypts PII in-band via AES-256-GCM. Zero Redis dependency.

### B. Machine-to-Machine (JSON-RPC / Tool Calls)
When the proxy detects structured AI tool calls or JSON-RPC `2.0` payloads, it **bypasses your configuration** and strictly enforces **STATELESS_CRYPTO**. 
* **Why?** Substituting data with synthetic fakes or changing string lengths inside strict JSON payloads frequently breaks the JSON syntax tree, causing agent crashes.
* **The Solution:** By forcing in-band AES encryption for machine traffic, the proxy guarantees mathematically reversible masking without mutating the JSON structure or relying on Redis.

---

### 🔥 Enterprise Flagship Features"""

# 3. Inject the new section above "### 🔥 Enterprise Flagship Features"
text = text.replace("### 🔥 Enterprise Flagship Features", new_section)

# Remove any double "---" created during replacement
text = re.sub(r"---\n\n---", "---", text)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
