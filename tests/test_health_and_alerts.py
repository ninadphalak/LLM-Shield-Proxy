"""Integration tests for Health Probes and Prometheus Alerts."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app

client = TestClient(app)


@pytest.mark.asyncio
async def test_livez_endpoint():
    """Verify the /livez alias group returns instantly.

    ``name_redaction`` rides along on the liveness response because ``/health`` is what an
    operator curls by hand, and Tier 3 has no heuristic fallback: without a loaded NER
    model no PERSON span is produced at all. Liveness itself is unaffected -- a proxy with
    no NER model is running correctly, it just is not redacting names.
    """
    from llm_shield_proxy.engines.pii_engine import pii_engine

    expected_ner = "ok" if pii_engine.name_redaction_active else "unavailable"
    for path in ["/livez", "/health", "/healthz"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "name_redaction": expected_ner}


@pytest.mark.asyncio
async def test_health_reports_name_redaction_unavailable_without_a_model():
    """The conftest suite runs with ENABLE_TIER3_ONNX_NER off, so this is the real default.

    The assertion is the point of the whole change: an operator must not be able to read
    a healthy response and conclude that names are being redacted.
    """
    from llm_shield_proxy.engines.pii_engine import pii_engine

    assert pii_engine.name_redaction_active is False
    assert client.get("/health").json()["name_redaction"] == "unavailable"


@pytest.mark.asyncio
async def test_readyz_names_the_profiles_that_expect_ner_and_get_none():
    """/readyz carries a warning naming each profile whose PERSON declaration is inert."""
    from llm_shield_proxy.engines.pii_engine import pii_engine

    assert pii_engine.name_redaction_active is False
    body = client.get("/readyz").json()

    assert body["components"]["name_redaction"] == "unavailable"
    warnings = body.get("warnings", [])
    assert warnings, "an unbacked PERSON declaration must be surfaced on /readyz"
    warning = next(w for w in warnings if w["component"] == "name_redaction")
    assert "global_strict" in warning["profiles_declaring_it"]


@pytest.mark.asyncio
async def test_name_redaction_is_not_part_of_the_readiness_gate():
    """Reported, not enforced.

    Most deployments run without a NER model. Returning 503 for all of them would make
    the signal useless, so the gate stays on pii_engine/vault/redis.
    """

    async def mock_healthy():
        return True

    import llm_shield_proxy.api.health as health_module

    original = (health_module._check_pii_engine, health_module._check_vault, health_module._check_redis)
    health_module._check_pii_engine = mock_healthy
    health_module._check_vault = mock_healthy
    health_module._check_redis = mock_healthy
    health_module._readyz_cache.clear()
    try:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["components"]["name_redaction"] == "unavailable"
    finally:
        (
            health_module._check_pii_engine,
            health_module._check_vault,
            health_module._check_redis,
        ) = original
        health_module._readyz_cache.clear()


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
    health._readyz_cache["result"] = {"status_code": 200, "content": {"cached": "data"}}
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
    """Fast source-file smoke check on the PrometheusRule template.

    This does **not** render the chart: it strips every `{{ ... }}` with a regex
    and parses what is left, so it cannot detect a chart that fails to render, a
    PromQL expression Prometheus rejects, or a metric the app never exports. It
    is kept only because it needs no toolchain. The real verification --
    `helm template` plus `promtool check rules` against the rendered output --
    lives in `tests/test_helm_render_and_alerts.py`, and it found this file's
    template does not in fact render as shipped.
    """
    helm_path = (
        Path(__file__).parent.parent / "deploy" / "helm" / "llm-shield-proxy" / "templates" / "prometheus-rule.yaml"
    )
    assert helm_path.exists(), "prometheus-rule.yaml not found"

    content = helm_path.read_text(encoding="utf-8")

    # We must strip the helm templating logic before YAML parsing
    import re

    # Remove complete blocks like {{- if ... }} and {{- end }} that are on their own lines
    content = re.sub(r"^\s*\{\{.*\}\}\s*$", "", content, flags=re.MULTILINE)
    # Replace inline template tags with a dummy string to keep valid YAML
    content = re.sub(r"\{\{.*?\}\}", "dummy_value", content)

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
