import os
import re

base_dir = "c:/git_repo/LLM-Shield-Proxy"

# 1. Add grpclib to pyproject.toml
pyproject_path = os.path.join(base_dir, "pyproject.toml")
with open(pyproject_path, "r", encoding="utf-8") as f:
    content = f.read()
if '"grpclib>=0.4.3"' not in content:
    content = content.replace('"cachetools>=5.3.0"\n]', '"cachetools>=5.3.0",\n    "grpclib>=0.4.3"\n]')
    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(content)


# 2. Fix hardcoded path in test_health_and_alerts.py
test_health_path = os.path.join(base_dir, "tests/test_health_and_alerts.py")
with open(test_health_path, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace(
    'helm_path = Path("c:/git_repo/LLM-Shield-Proxy/deploy/helm/llm-shield-proxy/templates/prometheus-rule.yaml")',
    'helm_path = Path(__file__).parent.parent / "deploy" / "helm" / "llm-shield-proxy" / "templates" / "prometheus-rule.yaml"'
)
with open(test_health_path, "w", encoding="utf-8") as f:
    f.write(content)


# 3. Disable Vault and Redis in conftest.py to prevent 503 readyz errors in CI
conftest_path = os.path.join(base_dir, "tests/conftest.py")
with open(conftest_path, "r", encoding="utf-8") as f:
    content = f.read()

settings_updates = """    settings.UPSTREAM_BASE_URL = "https://api.openai.com"
    settings.ENABLE_REDIS_RATE_LIMIT = False
    settings.ENABLE_VAULT_SECRETS = False
    settings.ENABLE_TIER3_ONNX_NER = False"""

content = content.replace('    settings.UPSTREAM_BASE_URL = "https://api.openai.com"', settings_updates)

with open(conftest_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Fixes applied successfully.")
