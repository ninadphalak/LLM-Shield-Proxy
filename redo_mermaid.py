import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update Dual-Pipeline Diagram
old_dual_pattern = r"```mermaid\nflowchart TD\n    classDef stateful.*?Tag -\.-\> Redis\n```"

new_dual = """```mermaid
flowchart TD
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
```"""
text = re.sub(old_dual_pattern, new_dual, text, flags=re.DOTALL)

# 2. Add examples to the Markdown text for the 4 modes
# The text likely contains a list: "1. SYNTHETIC (Stateful)", "2. STRUCTURAL_TAG (Stateful)"
text = text.replace("**SYNTHETIC (Stateful):**", "**SYNTHETIC (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is 111-11-1111'*).")
text = text.replace("**STRUCTURAL_TAG (Stateful):**", "**STRUCTURAL_TAG (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [SSN_1]'*).")
text = text.replace("**SCRUB (Stateless):**", "**SCRUB (Stateless):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is ***'*).")
text = text.replace("**STATELESS_SYNTHETIC (Stateless):**", "**STATELESS_SYNTHETIC (Stateless):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [enc_3x9kL]'*).")


# 3. Update Main Architecture Diagram
old_main_pattern = r"## 🏗️ Architecture Diagram.*?### How It Works"

new_main = """## 🏗️ Architecture Diagram

```mermaid
flowchart LR
    Client["Browser / IDE / LangChain"]
    LLM["OpenAI / Claude / Gemini"]
    
    subgraph Proxy ["🛡️ LLM-Shield-Proxy (VPC)"]
        Auth["🔑 Auth"]
        Router{"JSON-RPC?"}
        
        subgraph Engine ["🔒 Redaction Engine"]
            direction TB
            T1["Tier 1: Regex"]
            T2["Tier 2: Entropy"]
            T3["Tier 3: ONNX NER"]
            T1 --> T2 --> T3
        end
        
        Redis[("Redis Vault")]
        AES["Stateless AES"]
    end
    
    Client -->|1. Prompt| Auth
    Auth --> Router
    Router -->|Text| Engine
    Router -->|Agent| AES
    
    Engine --> Redis
    Engine --> AES
    
    Redis -->|2. Sanitized| LLM
    AES -->|2. Sanitized| LLM
    
    LLM -.->|3. Stream| Redis
    LLM -.->|3. Stream| AES
    
    Redis -.->|4. Rehydrated| Client
    AES -.->|4. Rehydrated| Client
```

### How It Works"""
text = re.sub(old_main_pattern, new_main, text, flags=re.DOTALL)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
