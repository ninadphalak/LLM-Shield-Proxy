"""Helm charts and Prometheus alert rules, verified against a real render.

The previous test read `prometheus-rule.yaml` off disk, stripped every `{{ ... }}`
with a regex, and parsed what was left. That checks that a string contains three
alert names. It cannot catch a chart that does not render, an alert expression
that PromQL rejects, a metric name the application never exports, or a probe
path the application does not serve -- and three of those four were in fact
broken when this module was first run. Two of them (the PrometheusRule that did
not render, the probe paths the app does not serve) were chart defects and are
now fixed; these tests are what hold them fixed.

These tests shell out to the real `helm` and `promtool` binaries. They skip when
those are absent; `SHIELD_REQUIRE_HELM=1` (set by CI) turns the skip into a
failure so the job cannot pass without having actually rendered the chart.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_CHART = REPO_ROOT / "charts" / "llm-shield-proxy"
DEPLOY_CHART = REPO_ROOT / "deploy" / "helm" / "llm-shield-proxy"
REQUIRE_HELM = os.environ.get("SHIELD_REQUIRE_HELM") == "1"

_MISSING = [tool for tool in ("helm", "promtool") if shutil.which(tool) is None]

if _MISSING and REQUIRE_HELM:
    raise RuntimeError(
        f"SHIELD_REQUIRE_HELM=1 but these are not on PATH: {_MISSING}. "
        "Refusing to skip: this module exists to render the real chart."
    )

pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"not on PATH: {', '.join(_MISSING)}"
)

EXPECTED_ALERTS = {
    "LLMShieldDLPFailureRateSpike",
    "LLMShieldLookaheadBufferBackpressure",
    "LLMShieldVaultAuthExpiry",
}


def _helm_template(chart: Path, *set_args: str) -> subprocess.CompletedProcess:
    command = ["helm", "template", "release", str(chart)]
    for arg in set_args:
        command += ["--set", arg]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _render(chart: Path, *set_args: str) -> list[dict[str, Any]]:
    result = _helm_template(chart, *set_args)
    assert result.returncode == 0, (
        f"helm template failed for {chart.name} {set_args}:\n{result.stderr}"
    )
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    assert documents, "helm rendered no manifests"
    return documents


def _by_kind(documents: Iterable[dict], kind: str) -> list[dict]:
    return [doc for doc in documents if doc.get("kind") == kind]


# ---------------------------------------------------------------------------
# The charts render at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chart", [APP_CHART, DEPLOY_CHART], ids=["app-chart", "deploy-chart"])
def test_chart_lints(chart):
    result = subprocess.run(
        ["helm", "lint", str(chart)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "chart,set_args",
    [
        (APP_CHART, ()),
        (DEPLOY_CHART, ()),
        (DEPLOY_CHART, ("webhook.enabled=true",)),
        (DEPLOY_CHART, ("mTLS.enabled=true",)),
    ],
    ids=["app-default", "deploy-default", "deploy-webhook", "deploy-mtls"],
)
def test_chart_renders_to_valid_manifests(chart, set_args):
    """Every rendered document must be parseable YAML with an apiVersion/kind."""
    documents = _render(chart, *set_args)

    for doc in documents:
        assert doc.get("apiVersion"), doc
        assert doc.get("kind"), doc
        assert doc.get("metadata", {}).get("name"), doc

    assert _by_kind(documents, "Deployment"), "no Deployment in the render"
    assert _by_kind(documents, "Service"), "no Service in the render"


def test_webhook_render_wires_one_ca_across_serving_cert_and_admission_bundle():
    """The webhook is inert if its caBundle does not match its serving cert."""
    documents = _render(DEPLOY_CHART, "webhook.enabled=true")

    webhooks = _by_kind(documents, "MutatingWebhookConfiguration")
    assert webhooks, "webhook.enabled=true rendered no MutatingWebhookConfiguration"

    ca_bundles = {
        hook["clientConfig"]["caBundle"]
        for config in webhooks
        for hook in config["webhooks"]
        if "caBundle" in hook.get("clientConfig", {})
    }
    assert len(ca_bundles) == 1 and next(iter(ca_bundles)), ca_bundles

    paths = {
        hook["clientConfig"]["service"]["path"]
        for config in webhooks
        for hook in config["webhooks"]
    }
    assert paths == {"/v1/k8s/mutate"}, f"admission path does not match the app route: {paths}"


# ---------------------------------------------------------------------------
# Prometheus alert rules
# ---------------------------------------------------------------------------


def test_shipped_chart_renders_the_prometheus_rule():
    """The chart as shipped must render its PrometheusRule.

    This used to fail: prometheus-rule.yaml calls the "llm-shield-proxy.labels"
    helper, and that chart's _helpers.tpl defined only .name and .fullname, so
    `helm template --set prometheus.prometheusRule.enabled=true` errored out and
    the alert rules could not be installed from the chart at all. `helm lint`
    passed because the rule is disabled by default. The helper is now defined.
    """
    result = _helm_template(DEPLOY_CHART, "prometheus.prometheusRule.enabled=true")
    assert result.returncode == 0, result.stderr


def test_rendered_prometheus_rule_carries_the_common_labels():
    """The labels helper must emit real label pairs, not an empty block."""
    documents = _render(DEPLOY_CHART, "prometheus.prometheusRule.enabled=true")
    rule = _by_kind(documents, "PrometheusRule")[0]
    labels = rule["metadata"]["labels"]

    assert labels.get("app.kubernetes.io/name") == "llm-shield-proxy"
    assert labels.get("app.kubernetes.io/instance") == "release"
    assert labels.get("app.kubernetes.io/managed-by") == "Helm"
    assert labels.get("helm.sh/chart", "").startswith("llm-shield-proxy-")


@pytest.fixture
def prometheus_rule() -> dict:
    """The PrometheusRule from the chart exactly as shipped.

    No harness repair: the chart renders this on its own now.
    """
    documents = _render(DEPLOY_CHART, "prometheus.prometheusRule.enabled=true")
    rules = _by_kind(documents, "PrometheusRule")
    assert len(rules) == 1, f"expected exactly one PrometheusRule, got {len(rules)}"
    return rules[0]


def test_rendered_prometheus_rule_declares_the_documented_alerts(prometheus_rule):
    alerts = {
        rule["alert"]
        for group in prometheus_rule["spec"]["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    assert EXPECTED_ALERTS <= alerts, f"missing: {EXPECTED_ALERTS - alerts}"


def test_rendered_alert_expressions_pass_promtool(prometheus_rule, tmp_path):
    """Real PromQL validation of the rendered rules, not a YAML shape check."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        yaml.safe_dump({"groups": prometheus_rule["spec"]["groups"]}, sort_keys=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["promtool", "check", "rules", str(rules_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, (
        f"promtool rejected the rendered alert rules:\n{result.stdout}\n{result.stderr}"
    )
    assert "SUCCESS" in result.stdout.upper(), result.stdout


def test_alert_expressions_are_complete_rules(prometheus_rule):
    """Each alert needs an expr, a `for` window, a severity and an annotation."""
    for group in prometheus_rule["spec"]["groups"]:
        for rule in group["rules"]:
            name = rule.get("alert")
            assert name, rule
            assert rule.get("expr", "").strip(), f"{name} has no expression"
            assert rule.get("for"), f"{name} has no `for` window, so it fires on a single scrape"
            assert rule.get("labels", {}).get("severity") in {"critical", "warning", "info"}, rule
            assert rule.get("annotations", {}).get("summary"), f"{name} has no summary annotation"


def test_alert_expressions_only_reference_metrics_the_app_exports(prometheus_rule):
    """An alert on a metric that is never emitted is silently dead.

    This is the check the string-stripping test could not make: it compares the
    rendered PromQL against the application's live Prometheus registry.
    """
    from prometheus_client import REGISTRY

    import llm_shield_proxy.observability.metrics  # noqa: F401  (registers the collectors)

    exported: set[str] = set()
    for metric in REGISTRY.collect():
        exported.add(metric.name)
        if metric.type == "counter":
            exported.add(f"{metric.name}_total")
        elif metric.type == "histogram":
            exported.update(
                {f"{metric.name}_bucket", f"{metric.name}_sum", f"{metric.name}_count"}
            )
        for sample in metric.samples:
            exported.add(sample.name)

    referenced: set[str] = set()
    for group in prometheus_rule["spec"]["groups"]:
        for rule in group["rules"]:
            referenced.update(re.findall(r"\b(?:llm_shield|shield_proxy|audit)_\w+", rule["expr"]))

    assert referenced, "no application metric names appear in the alert expressions"
    unknown = referenced - exported
    assert not unknown, (
        f"alert rules reference metrics the application never exports: {sorted(unknown)}\n"
        f"exported: {sorted(n for n in exported if 'shield' in n)}"
    )


# ---------------------------------------------------------------------------
# Health probes, checked against the routes the app actually serves
# ---------------------------------------------------------------------------


def _app_routes() -> set[str]:
    """Every concrete path the application serves.

    Read from the OpenAPI schema rather than ``app.routes``, because routes
    added via ``include_router`` are wrapped and expose no ``path`` at the top
    level. Templated paths are dropped: the catch-all ``/{path}`` would match a
    mistyped probe path, but it is authenticated and never answers a kubelet
    with 200, so it must not count as "the app serves this".
    """
    import warnings

    from llm_shield_proxy.api.main import app

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paths = app.openapi()["paths"].keys()
    return {path for path in paths if "{" not in path}


def _probe_paths(documents: Iterable[dict]) -> set[str]:
    paths: set[str] = set()
    for deployment in _by_kind(documents, "Deployment"):
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
                spec = container.get(probe)
                if spec and "httpGet" in spec:
                    paths.add(spec["httpGet"]["path"])
    return paths


def test_app_chart_probe_paths_are_routes_the_application_serves():
    paths = _probe_paths(_render(APP_CHART))
    assert paths, "the app chart rendered no HTTP probes"

    unknown = paths - _app_routes()
    assert not unknown, f"probe paths with no matching route: {sorted(unknown)}"


def test_deploy_chart_probe_paths_are_routes_the_application_serves():
    """The deploy chart's probes must hit routes the app actually serves.

    This used to fail: the Deployment probed /health/ready and /health/live,
    which fall through to the authenticated catch-all, so a pod from this chart
    never became Ready and was then killed by its own liveness probe. It now
    probes /readyz and /livez.
    """
    paths = _probe_paths(_render(DEPLOY_CHART))
    assert paths, "the deploy chart rendered no HTTP probes"

    unknown = paths - _app_routes()
    assert not unknown, f"probe paths with no matching route: {sorted(unknown)}"


def test_probe_paths_are_reported_for_diagnosis(record_property):
    """Records both charts' probe paths and the real routes in the test report."""
    record_property("app_chart_probes", json.dumps(sorted(_probe_paths(_render(APP_CHART)))))
    record_property("deploy_chart_probes", json.dumps(sorted(_probe_paths(_render(DEPLOY_CHART)))))
    record_property("application_routes", json.dumps(sorted(_app_routes())))
