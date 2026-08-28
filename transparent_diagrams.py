import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update Dual-Pipeline Diagram
old_dual_pattern = r"```mermaid\nflowchart TD\n    Client.*?Tag -\.-\> Redis\n```"

new_dual = """```mermaid
flowchart TD
    classDef default fill:transparent,stroke:#888,stroke-width:1px

    Client["Browser / IDE / LangChain"] --> Router{"JSON-RPC?"}
    
    Router -->|No: Text| SubA
    Router -->|Yes: Agent| SubB
    
    subgraph SubA [A. Human-to-LLM]
        direction TB
        Syn["1. SYNTHETIC"]
        Tag["2. STRUCTURAL_TAG"]
        Scrub["3. SCRUB"]
        CryptoA["4. STATELESS_SYNTHETIC"]
    end

    subgraph SubB [B. Machine-to-Machine]
        direction TB
        CryptoB["STATELESS_SYNTHETIC"]
    end
    
    Syn -.-> Redis[(Redis Vault)]
    Tag -.-> Redis

    style SubA fill:transparent,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
    style SubB fill:transparent,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
```"""
text = re.sub(old_dual_pattern, new_dual, text, flags=re.DOTALL)

# 2. Update Main Architecture Diagram
old_main_pattern = r"```mermaid\nflowchart LR\n    Client.*?AES -\.-\>\|4. Rehydrated\| Client\n```"

new_main = """```mermaid
flowchart TD
    classDef default fill:transparent,stroke:#888,stroke-width:1px

    Client["Browser / IDE / LangChain"]
    
    subgraph Proxy ["🛡️ LLM-Shield-Proxy (VPC)"]
        direction TD
        
        Auth["🔑 Auth"]
        Router{"JSON-RPC?"}
        
        subgraph Engine ["🔒 Redaction Engine"]
            direction LR
            T1["Tier 1: Regex"] --> T2["Tier 2: Entropy"] --> T3["Tier 3: ONNX NER"]
        end
        
        Redis[("Redis Vault")]
        AES["Stateless AES"]
        
        Auth --> Router
        Router -->|Text| Engine
        Router -->|Agent| AES
        
        Engine --> Redis
        Engine --> AES
    end
    
    LLM["OpenAI / Claude / Gemini"]
    
    style Proxy fill:transparent,stroke:#888,stroke-width:2px,stroke-dasharray: 5 5
    style Engine fill:transparent,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5

    Client -->|1. Prompt| Auth
    Redis -->|2. Sanitized| LLM
    AES -->|2. Sanitized| LLM
    
    LLM -.->|3. Stream| Redis
    LLM -.->|3. Stream| AES
    
    Redis -.->|4. Rehydrated| Client
    AES -.->|4. Rehydrated| Client
```"""
text = re.sub(old_main_pattern, new_main, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
