import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# Replace the text under Enterprise Security & Threat Defenses
old_text = r"LLM-Shield-Proxy is validated against an exhaustive suite of \*\*automated unit, integration, and adversarial fuzzing tests\*\*\."
new_text = "LLM-Shield-Proxy is validated against an exhaustive suite of **automated unit, integration, and adversarial fuzzing tests**. The proxy directly mitigates **8 out of the 10 vulnerabilities in the OWASP Top 10 for LLMs** (v1.1)."

text = re.sub(old_text, new_text, text, count=1)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
