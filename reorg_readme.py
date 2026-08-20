import re

with open('c:/git_repo/LLM-Shield-Proxy/README.md', 'r', encoding='utf-8') as f:
    content = f.read()

# The sections we need:
# 1. "## 🛡️ Enterprise Security & Threat Defenses" ... down to "---"
# 2. "## 🏗️ Architecture Diagram" ... "### How It Works (The Data Flow)" ... down to "---"
# 3. "## ⚡ Core Architecture & Technical Innovations" ...

# Let's extract the "Architecture Diagram" block.
# We'll assume it starts at "## 🏗️ Architecture Diagram" and ends at the next "---" or "## "
arch_diag_pattern = re.compile(r'(## 🏗️ Architecture Diagram.*?(?=\n---|\n## ⚡ |\Z))', re.DOTALL)
arch_diag_match = arch_diag_pattern.search(content)

if arch_diag_match:
    arch_diag_text = arch_diag_match.group(1).strip() + "\n\n---\n\n"
    # Remove it from the original content
    content = arch_diag_pattern.sub('', content)

    # We want to place it above "## ⚡ Core Architecture & Technical Innovations"
    core_arch_pattern = re.compile(r'(## ⚡ Core Architecture & Technical Innovations)')
    content = core_arch_pattern.sub(lambda m: arch_diag_text + m.group(1), content)


# Now, insert the Compliance section right after the Enterprise Security summary table.
# The Enterprise Security summary table ends at the Multi-Provider Adapters row.
sec_summary_end_pattern = re.compile(r'(\| \*\*🔄 Multi-Provider Adapters\*\* \|.*?)\n', re.DOTALL)

compliance_section = """
## 📜 Enterprise Compliance: Audit, Forensics & Legal

LLM-Shield-Proxy is engineered specifically to help enterprises utilize Generative AI without violating data privacy regulations like HIPAA or failing SOC 2 audits.

Below is a summary of our compliance mappings. For the exhaustive deep-dive mapping, view our [Enterprise Compliance Documentation](COMPLIANCE.md).

| Compliance Domain | Supported Features & Capabilities |
| :--- | :--- |
| **🏥 HIPAA Transmission Security** | Local O(1) Redaction, Tier-2 Shannon Entropy + Faker synthetic substituting. No raw PHI traverses public internet to third-party APIs. |
| **🛡️ SOC 2 Audit Controls** | WORM-Compliant Merkle Attestation & SHA-256 Hash Chaining. Emits tamper-evident structured logs with strict RFC 6902 differential patching. |
| **⚖️ Legal & Egress Provenance** | Cryptographic Proof of Non-Egress Merkle Attestation. Dynamic Canary Watermarking for insider leak forensics. |
| **🔐 Data Integrity & Storage** | Zero long-term storage. In-Band Stateless AES-256-GCM masking or ephemeral Redis TTL Vault mapping with Deterministic HMAC masking. |
"""

content = sec_summary_end_pattern.sub(lambda m: m.group(1) + "\n\n" + compliance_section.strip() + "\n", content)

with open('c:/git_repo/LLM-Shield-Proxy/README.md', 'w', encoding='utf-8') as f:
    f.write(content)

print("README reorganized successfully.")
