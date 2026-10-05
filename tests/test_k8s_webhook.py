import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_shield_proxy.api import webhook
from llm_shield_proxy.api.webhook import webhook_router
from llm_shield_proxy.core.config import settings


@pytest.fixture
def webhook_client():
    test_app = FastAPI()
    test_app.include_router(webhook_router)
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def restore_webhook_settings():
    original_token = settings.K8S_WEBHOOK_AUTH_TOKEN
    original_image = settings.K8S_SIDECAR_IMAGE
    settings.K8S_WEBHOOK_AUTH_TOKEN = None
    settings.K8S_SIDECAR_IMAGE = "registry.example/shield@sha256:test"
    yield
    settings.K8S_WEBHOOK_AUTH_TOKEN = original_token
    settings.K8S_SIDECAR_IMAGE = original_image


def _admission_review(*, labels=None, containers=None):
    return {
        "request": {
            "uid": "admission-123",
            "object": {
                "metadata": {"labels": labels or {}},
                "spec": {"containers": containers or [{"name": "application"}]},
            },
        }
    }


def test_matching_label_appends_only_the_configured_sidecar(webhook_client):
    response = webhook_client.post(
        "/v1/k8s/mutate",
        json=_admission_review(labels={"llm-shield.io/inject": "true"}),
    )

    assert response.status_code == 200
    admission_response = response.json()["response"]
    assert admission_response["uid"] == "admission-123"
    assert admission_response["allowed"] is True
    assert admission_response["patchType"] == "JSONPatch"

    patch = json.loads(base64.b64decode(admission_response["patch"]))
    assert patch == [
        {
            "op": "add",
            "path": "/spec/containers/-",
            "value": {
                "name": "llm-shield-proxy",
                "image": "registry.example/shield@sha256:test",
                "ports": [{"containerPort": 8000}],
                "env": [
                    {"name": "SHIELD_FAILURE_MODE", "value": "FAIL_CLOSED"},
                    {"name": "ENABLE_TIER3_ONNX_NER", "value": "false"},
                ],
                "resources": {
                    "limits": {"memory": "256Mi", "cpu": "500m"},
                    "requests": {"memory": "128Mi", "cpu": "100m"},
                },
                # Without a readiness probe the pod was Ready while the sidecar was still
                # starting, and the app's first requests to 127.0.0.1:8000 were refused.
                "readinessProbe": {
                    "httpGet": {"path": "/readyz", "port": 8000},
                    "initialDelaySeconds": 2,
                    "periodSeconds": 5,
                },
                "livenessProbe": {
                    "httpGet": {"path": "/livez", "port": 8000},
                    "initialDelaySeconds": 15,
                    "periodSeconds": 10,
                },
            },
        }
    ]


def test_sidecar_probes_use_the_paths_the_helm_deployment_uses():
    """One pair of health paths for both ways the proxy runs in a cluster."""
    chart = (Path(__file__).resolve().parents[1] / "deploy/helm/llm-shield-proxy/templates/deployment.yaml").read_text()
    [patch] = webhook._build_sidecar_patch()
    for probe in ("readinessProbe", "livenessProbe"):
        assert patch["value"][probe]["httpGet"]["path"] in chart
        assert patch["value"][probe]["httpGet"]["port"] == 8000


def test_nonmatching_label_returns_no_patch(webhook_client):
    response = webhook_client.post("/v1/k8s/mutate", json=_admission_review())

    assert response.status_code == 200
    assert response.json()["response"] == {"uid": "admission-123", "allowed": True}


def test_existing_same_name_container_is_not_duplicated(webhook_client):
    response = webhook_client.post(
        "/v1/k8s/mutate",
        json=_admission_review(
            labels={"llm-shield.io/inject": "true"},
            containers=[{"name": "application"}, {"name": "llm-shield-proxy"}],
        ),
    )

    assert response.status_code == 200
    assert response.json()["response"] == {"uid": "admission-123", "allowed": True}


def test_configured_token_is_enforced(webhook_client):
    settings.K8S_WEBHOOK_AUTH_TOKEN = "expected-token"

    missing = webhook_client.post("/v1/k8s/mutate", json=_admission_review())
    accepted = webhook_client.post(
        "/v1/k8s/mutate",
        headers={"x-webhook-token": "expected-token"},
        json=_admission_review(),
    )

    assert missing.status_code == 401
    assert accepted.status_code == 200


def test_helm_webhook_contract_matches_fastapi_route_and_mounts_tls():
    repo_root = Path(__file__).resolve().parents[1]
    webhook_template = (repo_root / "deploy/helm/llm-shield-proxy/templates/mutating-webhook.yaml").read_text()
    deployment_template = (repo_root / "deploy/helm/llm-shield-proxy/templates/deployment.yaml").read_text()

    assert 'path: "/v1/k8s/mutate"' in webhook_template
    assert "caBundle: {{ $caCert }}" in webhook_template
    assert 'lookup "v1" "Secret"' in webhook_template
    assert '"--tls-cert-file"' in deployment_template
    assert '"--tls-key-file"' in deployment_template
    assert "secretName: {{ include \"llm-shield-proxy.fullname\" . }}-webhook-cert" in deployment_template


def test_webhook_is_not_served_unless_enabled():
    """It was mounted on every install and unauthenticated by default."""
    from llm_shield_proxy.api.main import app as default_app

    assert settings.ENABLE_K8S_WEBHOOK is False
    paths = {getattr(route, "path", None) for route in default_app.routes}
    assert "/v1/k8s/mutate" not in paths
    response = TestClient(default_app).post("/v1/k8s/mutate", json=_admission_review())
    assert response.status_code != 200 or "patch" not in response.text


def test_helm_turns_the_webhook_route_on_with_the_webhook():
    repo_root = Path(__file__).resolve().parents[1]
    deployment_template = (repo_root / "deploy/helm/llm-shield-proxy/templates/deployment.yaml").read_text()
    assert "- name: ENABLE_K8S_WEBHOOK\n              value: {{ .Values.webhook.enabled | quote }}" in deployment_template


def _injected_container(client, annotations=None):
    review = _admission_review(labels={"llm-shield.io/inject": "true"})
    if annotations is not None:
        review["request"]["object"]["metadata"]["annotations"] = annotations
    response = client.post("/v1/k8s/mutate", json=review)
    return json.loads(base64.b64decode(response.json()["response"]["patch"]))[0]["value"]


def test_sidecar_loads_its_keys_from_the_configured_secret(webhook_client, monkeypatch):
    """Without keys the injected sidecar answered every request with a 401."""
    monkeypatch.setattr(settings, "K8S_SIDECAR_SECRET_NAME", "shield-keys")
    assert _injected_container(webhook_client)["envFrom"] == [{"secretRef": {"name": "shield-keys"}}]


def test_pod_annotation_chooses_the_keys_secret(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "K8S_SIDECAR_SECRET_NAME", "shield-keys")
    container = _injected_container(webhook_client, {"llm-shield.io/keys-secret": "team-a-keys"})
    assert container["envFrom"] == [{"secretRef": {"name": "team-a-keys"}}]


def test_an_invalid_secret_name_is_not_copied_into_the_patch(webhook_client, monkeypatch):
    monkeypatch.setattr(settings, "K8S_SIDECAR_SECRET_NAME", None)
    container = _injected_container(webhook_client, {"llm-shield.io/keys-secret": "x\"}], \"y"})
    assert "envFrom" not in container
