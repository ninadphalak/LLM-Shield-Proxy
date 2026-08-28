import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

old_diagram_pattern = r"```mermaid\nflowchart TD.*?CryptoB -\.-\> AES\n```"

new_diagram = """```mermaid
flowchart TD
    classDef stateful fill:#fffbeb,stroke:#f59e0b,stroke-width:2px,color:#92400e
    classDef stateless fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a
    classDef router fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#0f172a

    Client["Client App<br/><i>'My SSN is 000-00-0000'</i>"] --> Router{"JSON-RPC?"}:::router
    
    Router -->|No: Text| SubA
    Router -->|Yes: Agent| SubB
    
    subgraph SubA [A. Human-to-LLM (Choose One Config)]
        direction TB
        Syn["1. SYNTHETIC<br/><i>'...is 111-11-1111'</i>"]:::stateful
        Tag["2. STRUCTURAL_TAG<br/><i>'...is [SSN_1]'</i>"]:::stateful
        Scrub["3. SCRUB<br/><i>'...is ***'</i>"]:::stateless
        CryptoA["4. STATELESS_SYNTHETIC<br/><i>'...is [enc_3x9kL]'</i>"]:::stateless
    end

    subgraph SubB [B. Machine-to-Machine]
        direction TB
        CryptoB["Strictly Forces<br/>STATELESS_SYNTHETIC"]:::stateless
    end
    
    Syn -.-> Redis[("Redis Vault")]:::stateful
    Tag -.-> Redis
```"""

text = re.sub(old_diagram_pattern, new_diagram, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
