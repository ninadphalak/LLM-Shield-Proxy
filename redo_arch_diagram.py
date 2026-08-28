import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# Replace Main Architecture Diagram
old_main_pattern = r"## 🏗️ Architecture Diagram.*?### How It Works"

new_main = """## 🏗️ Architecture Diagram

```mermaid
flowchart LR
    %% Nodes
    Client["Browser / IDE"]
    LLM["OpenAI / Claude"]
    
    subgraph Proxy ["🛡️ LLM-Shield-Proxy (VPC)"]
        direction LR
        
        subgraph Inbound ["Sanitization (Inbound)"]
            direction TB
            Engine["3-Tier Redaction Engine"]
            Vault["Vault (Redis / AES)"]
            Engine --> Vault
        end
        
        subgraph Outbound ["Rehydration (Outbound)"]
            direction TB
            Buffer["SSE Sliding-Window Buffer"]
            Rehydrator["Stream Rehydrator"]
            Buffer --> Rehydrator
        end
        
        Vault -.->|State Lookup| Rehydrator
    end
    
    %% Flows
    Client == "1. Raw Prompt" ==> Engine
    Vault == "2. Sanitized Egress" ==> LLM
    LLM == "3. SSE Stream" ==> Buffer
    Rehydrator == "4. Rehydrated Return" ==> Client
    
    %% Styles
    classDef default fill:transparent,stroke:#888,stroke-width:1px
    style Proxy fill:transparent,stroke:#888,stroke-width:2px,stroke-dasharray: 5 5
    style Inbound fill:transparent,stroke:#666,stroke-width:1px,stroke-dasharray: 5 5
    style Outbound fill:transparent,stroke:#666,stroke-width:1px,stroke-dasharray: 5 5
```

### How It Works"""
text = re.sub(old_main_pattern, new_main, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
