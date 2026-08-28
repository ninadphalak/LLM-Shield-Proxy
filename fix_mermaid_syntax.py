import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

old_diagram_pattern = r"```mermaid\nflowchart TD.*?Tag -\.-\> Redis\n```"

new_diagram = """```mermaid
flowchart TD
    classDef stateful fill:#fffbeb,stroke:#f59e0b,stroke-width:2px,color:#92400e
    classDef stateless fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a
    classDef router fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#0f172a

    Client["User Browser/IDE/App<br/>'My SSN is 000-00-0000'"] --> Router{"JSON-RPC?"}:::router
    
    subgraph SubA [A. Human-to-LLM - Choose One Config]
        direction TB
        Syn["1. SYNTHETIC<br/>'...is 111-11-1111'"]:::stateful
        Tag["2. STRUCTURAL_TAG<br/>'...is [SSN_1]'"]:::stateful
        Scrub["3. SCRUB<br/>'...is ***'"]:::stateless
        CryptoA["4. STATELESS_CRYPTO<br/>'...is [enc_3x9kL]'"]:::stateless
    end

    subgraph SubB [B. Machine-to-Machine]
        direction TB
        CryptoB["Strictly Forces<br/>STATELESS_CRYPTO"]:::stateless
    end
    
    Router -->|No: Text| SubA
    Router -->|Yes: Agent| SubB
    
    Syn -.-> Redis[(Redis Vault)]:::stateful
    Tag -.-> Redis
```"""

text = re.sub(old_diagram_pattern, new_diagram, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
