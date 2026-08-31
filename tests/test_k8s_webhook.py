import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
                    "limits": {"memory": "60Mi", "cpu": "200m"},
                    "requests": {"memory": "25Mi", "cpu": "50m"},
                },
            },
        }
    ]


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
