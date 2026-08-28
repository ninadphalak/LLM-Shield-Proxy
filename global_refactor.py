import os
import re

REPO_DIR = r"c:\git_repo\LLM-Shield-Proxy"

def replace_in_file(filepath, old_str, new_str):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if old_str in content:
        content = content.replace(old_str, new_str)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {filepath}")

# 1. Global Rename of STATELESS_SYNTHETIC to STATELESS_SYNTHETIC
for root, dirs, files in os.walk(REPO_DIR):
    if '.git' in root or '__pycache__' in root or '.pytest_cache' in root:
        continue
    
    for file in files:
        if file.endswith(('.py', '.md', '.yaml', '.yml', '.env')):
            filepath = os.path.join(root, file)
            replace_in_file(filepath, "STATELESS_SYNTHETIC", "STATELESS_SYNTHETIC")
            replace_in_file(filepath, "Stateless Synthetic", "Stateless Synthetic")
            replace_in_file(filepath, "stateless synthetic", "stateless synthetic")
            replace_in_file(filepath, "stateless_synthetic", "stateless_synthetic")

# Rename test file
old_test = os.path.join(REPO_DIR, "tests", "test_stateless_synthetic.py")
new_test = os.path.join(REPO_DIR, "tests", "test_stateless_synthetic.py")
if os.path.exists(old_test):
    os.rename(old_test, new_test)
    print(f"Renamed {old_test} to {new_test}")

# 2. Fix README.md Diagrams and formatting
readme_path = os.path.join(REPO_DIR, "README.md")
with open(readme_path, "r", encoding="utf-8") as f:
    readme = f.read()

# Remove dangling table
dangling_table_pattern = r"---\s+\|\s+:---\s+\|\s+:---\s+\|\s+:---\s+\|\n\| \*\*Synthetic Swapping.*?Legacy compliance pipelines, deterministic regex auditing \|"
readme = re.sub(dangling_table_pattern, "", readme, flags=re.DOTALL)

# Replace Dual-Pipeline Diagram
old_dual_pipeline_pattern = r"```mermaid\nflowchart TD\n    classDef stateful.*?Tag -\.-\> Redis\n```"

new_dual_pipeline = """```mermaid
flowchart TD
    classDef stateful stroke:#f59e0b,stroke-width:2px,color:#d97706
    classDef stateless stroke:#3b82f6,stroke-width:2px,color:#2563eb
    classDef router stroke:#64748b,stroke-width:2px,color:#475569

    Client["User Browser/IDE/App<br/><i>'My SSN is 000-00-0000'</i>"] --> Router{"JSON-RPC?"}:::router
    
    subgraph SubA [A. Human-to-LLM]
        direction TB
        Or["[OR]"]:::router
        Syn["1. SYNTHETIC<br/>'...is 111-11-1111'"]:::stateful
        Tag["2. STRUCTURAL_TAG<br/>'...is [SSN_1]'"]:::stateful
        Scrub["3. SCRUB<br/>'...is ***'"]:::stateless
        CryptoA["4. STATELESS_SYNTHETIC<br/>'...is [enc_3x9kL]'"]:::stateless
        
        Or --- Syn
        Or --- Tag
        Or --- Scrub
        Or --- CryptoA
    end

    subgraph SubB [B. Machine-to-Machine]
        direction TB
        CryptoB["Strictly Forces<br/>STATELESS_SYNTHETIC"]:::stateless
    end
    
    Router -->|No: Text| SubA
    Router -->|Yes: Agent| SubB
    
    Syn -.-> Redis[(Redis Vault)]:::stateful
    Tag -.-> Redis
```"""
readme = re.sub(old_dual_pipeline_pattern, new_dual_pipeline, readme, flags=re.DOTALL)


# Replace Main Architecture Diagram
old_main_arch_pattern = r"## 🏗️ Architecture Diagram.*?### How It Works"

new_main_arch = """## 🏗️ Architecture Diagram

```mermaid
flowchart TD
    classDef default stroke:#64748b,stroke-width:2px
    classDef proxy stroke:#3b82f6,stroke-width:2px
    classDef vault stroke:#f59e0b,stroke-width:2px
    classDef engine stroke:#10b981,stroke-width:2px,stroke-dasharray: 5 5

    UserApp["👤 Client App"]
    UpstreamLLM["☁️ Upstream LLM"]

    subgraph SecurityMoat ["🛡️ LLM-Shield-Proxy (Zero-Egress VPC)"]
        direction TD
        Auth["🔑 Inbound Auth"]:::proxy
        Router{"JSON-RPC?"}:::proxy
        
        subgraph CascadeEngine ["🔒 3-Tier Redaction Engine"]
            direction TB
            Tier1["Tier 1: Regex (Patterns)"]:::engine
            Tier2["Tier 2: Shannon Entropy (Secrets)"]:::engine
            Tier3["Tier 3: ONNX NER [Optional NLP Model]"]:::engine
            Tier1 --> Tier2 --> Tier3
        end
        
        Redis[("Redis (Stateful)")]:::vault
        AES["AES-GCM (Stateless)"]:::vault
        
    end

    %% Inbound Flow
    UserApp -->|1. Prompt| Auth
    Auth --> Router
    
    Router -->|No: Text| CascadeEngine
    Router -->|Yes: Agent| AES
    
    CascadeEngine -->|Store mapping| Redis
    CascadeEngine -->|Encrypt| AES
    
    Redis -->|2. Sanitized| UpstreamLLM
    AES -->|2. Sanitized| UpstreamLLM

    %% Outbound Flow
    UpstreamLLM -.->|3. Rehydrated SSE Stream| UserApp
    Redis -.->|Rehydrate| UpstreamLLM
    AES -.->|Decrypt| UpstreamLLM
```

### How It Works"""
readme = re.sub(old_main_arch_pattern, new_main_arch, readme, flags=re.DOTALL)

with open(readme_path, "w", encoding="utf-8") as f:
    f.write(readme)
