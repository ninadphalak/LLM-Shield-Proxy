"""Integration tests for Health Probes and Prometheus Alerts."""

import pytest
import yaml
from pathlib import Path

from fastapi.testclient import TestClient
from llm_shield_proxy.api.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_livez_endpoint():
    """Verify the /livez alias group returns instantly."""
    for path in ["/livez", "/health", "/healthz"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

@pytest.mark.asyncio
async def test_readyz_endpoint(monkeypatch):
    """Verify /readyz endpoint status structure."""
    
    # Mocking concurrent checks for healthy state
    async def mock_healthy():
        return True
    
    from llm_shield_proxy.api import health
    monkeypatch.setattr(health, "_check_pii_engine", mock_healthy)
    monkeypatch.setattr(health, "_check_vault", mock_healthy)
    monkeypatch.setattr(health, "_check_redis", mock_healthy)
    
    # Clear cache
    health._readyz_cache.clear()

    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["components"]["pii_engine"] == "ok"
    assert data["components"]["vault"] == "ok"
    assert data["components"]["redis"] == "ok"

    # Test cache logic
    health._readyz_cache["timestamp"] = 9999999999.0  # Future time
    health._readyz_cache["result"] = {
        "status_code": 200, 
        "content": {"cached": "data"}
    }
    response_cached = client.get("/readyz")
    assert response_cached.status_code == 200
    assert response_cached.json() == {"cached": "data"}
    
    # Mocking failure state
    health._readyz_cache.clear()
    async def mock_failed():
        return False
        
    monkeypatch.setattr(health, "_check_redis", mock_failed)
    
    response_failed = client.get("/readyz")
    assert response_failed.status_code == 503
    data_failed = response_failed.json()
    assert data_failed["status"] == "degraded"
    assert data_failed["components"]["redis"] == "degraded"
    assert data_failed["components"]["pii_engine"] == "ok"


def test_prometheus_rule_yaml():
    """Validate Kubernetes PrometheusRule CRD YAML rendering."""
    helm_path = Path("c:/git_repo/LLM-Shield-Proxy/deploy/helm/llm-shield-proxy/templates/prometheus-rule.yaml")
    assert helm_path.exists(), "prometheus-rule.yaml not found"
    
    content = helm_path.read_text(encoding="utf-8")
    
    # We must strip the helm templating logic before YAML parsing
    import re
    # Remove complete blocks like {{- if ... }} and {{- end }} that are on their own lines
    content = re.sub(r'^\s*\{\{.*\}\}\s*$', '', content, flags=re.MULTILINE)
    # Replace inline template tags with a dummy string to keep valid YAML
    content = re.sub(r'\{\{.*?\}\}', 'dummy_value', content)
    
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        pytest.fail(f"Prometheus rule is not valid YAML after stripping helm template tags: {e}")

    assert parsed["kind"] == "PrometheusRule"
    
    rules = parsed.get("spec", {}).get("groups", [])[0].get("rules", [])
    rule_names = [rule.get("alert") for rule in rules]
    
    assert "LLMShieldDLPFailureRateSpike" in rule_names
    assert "LLMShieldLookaheadBufferBackpressure" in rule_names
    assert "LLMShieldVaultAuthExpiry" in rule_names
