import re
import os

# 1. Update README.md (OWASP wording & Merkle -> Hash Chaining)
with open("README.md", "r", encoding="utf-8") as f:
    readme_text = f.read()

readme_text = readme_text.replace(
    "The proxy directly mitigates **8 out of the 10 vulnerabilities in the OWASP Top 10 for LLMs** (v1.1).",
    "The proxy directly mitigates **all 8 applicable vulnerabilities** in the OWASP Top 10 for LLMs (v1.1)."
)
readme_text = readme_text.replace("Merkle Attestation & Audit Logging", "Audit Logging & SHA-256 Hash Chaining")
readme_text = readme_text.replace("Merkle Attestation & SHA-256 Hash Chaining", "Cryptographic SHA-256 Hash Chaining")
readme_text = readme_text.replace("Merkle Attestation", "Cryptographic Attestation")

with open("README.md", "w", encoding="utf-8") as f:
    f.write(readme_text)


# 2. Update SECURITY.md (Merkle -> Hash Chaining)
with open("SECURITY.md", "r", encoding="utf-8") as f:
    security_text = f.read()

security_text = security_text.replace("Merkle Attestation & SHA-256 Hash Chaining", "Cryptographic SHA-256 Hash Chaining")
security_text = security_text.replace("Merkle Attestation", "Cryptographic Attestation")
security_text = security_text.replace("Merkle logs", "Hash-Chained logs")

with open("SECURITY.md", "w", encoding="utf-8") as f:
    f.write(security_text)


# 3. Update COMPLIANCE.md (Merkle -> Hash Chaining) if exists
if os.path.exists("COMPLIANCE.md"):
    with open("COMPLIANCE.md", "r", encoding="utf-8") as f:
        compliance_text = f.read()
    
    compliance_text = compliance_text.replace("Merkle Attestation & SHA-256 Hash Chaining", "Cryptographic SHA-256 Hash Chaining")
    compliance_text = compliance_text.replace("Merkle Attestation", "Cryptographic Attestation")
    compliance_text = compliance_text.replace("Merkle logs", "Hash-Chained logs")
    
    with open("COMPLIANCE.md", "w", encoding="utf-8") as f:
        f.write(compliance_text)


# 4. Update docs/operations.md (Clarify RAM & Merkle -> Hash Chaining)
with open(r"docs\operations.md", "r", encoding="utf-8") as f:
    ops_text = f.read()

ops_text = ops_text.replace(
    "If you are using the Stateful Redis Vault (`REDIS_URL`) instead of Stateless AES-256-GCM, you must provision enough Redis RAM to hold the deterministic session mappings.",
    "If you are using the Stateful Redis Vault (`REDIS_URL`), you must provision enough RAM on your **external Redis server**. (Note: This is completely separate from the proxy's internal `<85 MB` application memory footprint)."
)
ops_text = ops_text.replace("Merkle logs", "Hash-Chained logs")

with open(r"docs\operations.md", "w", encoding="utf-8") as f:
    f.write(ops_text)


# 5. Update docs/troubleshooting.md (Remove Anonymous Usage Tracking)
with open(r"docs\troubleshooting.md", "r", encoding="utf-8") as f:
    ts_text = f.read()

# Use regex to remove the entire Anonymous Usage Tracking section
ts_text = re.sub(
    r"## 📈 Anonymous Usage Tracking.*?---",
    "",
    ts_text,
    flags=re.DOTALL
)

with open(r"docs\troubleshooting.md", "w", encoding="utf-8") as f:
    f.write(ts_text)
