import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Simplify the dense text
dense_text = """* **Why?** Substituting data with synthetic fakes or changing string lengths inside strict JSON payloads frequently breaks the JSON syntax tree, causing agent crashes.
* **The Solution:** By forcing in-band AES encryption for machine traffic, the proxy guarantees mathematically reversible masking without mutating the JSON structure or relying on Redis."""

simple_text = """* **Why?** Replacing text with synthetic names often breaks JSON syntax and crashes agents. 
* **The Solution:** To fix this, the proxy uses in-band AES encryption for machine traffic—protecting the data without changing string lengths or relying on Redis."""

text = text.replace(dense_text, simple_text)

# 2. Overhaul the Architecture Diagram
old_diagram_pattern = r"## 🏗️ Architecture Diagram.*?### How It Works"

new_diagram = """## 🏗️ Architecture Diagram

```mermaid
flowchart TD
    classDef client fill:#f8fafc,stroke:#cbd5e1,stroke-width:2px,color:#0f172a
    classDef proxy fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a
    classDef vault fill:#fffbeb,stroke:#f59e0b,stroke-width:2px,color:#92400e

    UserApp["👤 Client App"]:::client
    UpstreamLLM["☁️ Upstream LLM"]:::client

    subgraph SecurityMoat ["🛡️ LLM-Shield-Proxy (Zero-Egress VPC)"]
        direction TD
        Auth["🔑 Inbound Auth"]:::proxy
        Router{"JSON-RPC?"}:::proxy
        
        Redaction["🔒 3-Tier Redaction Engine"]:::proxy
        
        Redis[("Redis (Stateful)")]:::vault
        AES["AES-GCM (Stateless)"]:::vault
        
        Buffer["⏱️ SSE Sliding-Window Buffer"]:::proxy
        Rehydrator["🔄 Stream Re-hydrator"]:::proxy
    end

    %% Inbound Flow
    UserApp -->|1. Prompt| Auth
    Auth --> Router
    
    Router -->|No: Text| Redaction
    Router -->|Yes: Agent| AES
    
    Redaction -->|Store mapping| Redis
    Redaction -->|Encrypt| AES
    
    Redis -->|2. Sanitized| UpstreamLLM
    AES -->|2. Sanitized| UpstreamLLM

    %% Outbound Flow
    UpstreamLLM -.->|3. SSE Stream| Buffer
    Buffer -.->|4. Safe Chunk| Rehydrator
    Rehydrator <-.->|5. Restore| Redis
    Rehydrator <-.->|5. Decrypt| AES
    Rehydrator -.->|6. Original Text| UserApp
```

### How It Works"""

text = re.sub(old_diagram_pattern, new_diagram, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
