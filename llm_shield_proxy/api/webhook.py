"""Kubernetes Mutating Admission Webhook.

Injects the LLM-Shield Proxy as a sidecar into Pods labeled with
llm-shield.io/inject: "true" using standard JSON Patch (RFC 6902).
The route runs inside the FastAPI process and adds normal request-processing overhead.
"""

import base64
import hmac
import json
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)

webhook_router = APIRouter(prefix="/v1/k8s", tags=["Kubernetes Webhook"])
security = HTTPBearer(auto_error=False)

async def verify_webhook_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    if settings.K8S_WEBHOOK_AUTH_TOKEN:
        token = None
        if credentials:
            token = credentials.credentials
        elif "x-webhook-token" in request.headers:
            token = request.headers["x-webhook-token"]

        # Constant-time comparison prevents timing-based brute-force of the webhook token
        if not token or not hmac.compare_digest(token, settings.K8S_WEBHOOK_AUTH_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid or missing webhook token")
    return True


# A Kubernetes object name (RFC 1123 subdomain). Anything else in the annotation is ignored
# rather than copied into the patch.
_SECRET_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
KEYS_SECRET_ANNOTATION = "llm-shield.io/keys-secret"  # nosec B105 - an annotation name, not a secret


def _keys_secret_for(annotations: Dict[str, Any]) -> Optional[str]:
    """The Secret the sidecar loads its keys from: the pod's annotation, else the setting."""
    name = annotations.get(KEYS_SECRET_ANNOTATION) or settings.K8S_SIDECAR_SECRET_NAME
    if isinstance(name, str) and _SECRET_NAME.fullmatch(name):
        return name
    return None


def _build_sidecar_patch(keys_secret: Optional[str] = None) -> list[Dict[str, Any]]:
    container: Dict[str, Any] = {
        "name": "llm-shield-proxy",
        "image": settings.K8S_SIDECAR_IMAGE,
        "ports": [{"containerPort": 8000}],
        "env": [
            {"name": "SHIELD_FAILURE_MODE", "value": "FAIL_CLOSED"},
            {"name": "ENABLE_TIER3_ONNX_NER", "value": "false"},
        ],
        # Resident memory is about 75-100 MiB idle; a 60Mi limit had the sidecar
        # killed for memory before it served a request.
        "resources": {
            "limits": {"memory": "256Mi", "cpu": "500m"},
            "requests": {"memory": "128Mi", "cpu": "100m"}
        }
    }
    # Without keys the sidecar answers every request with a 401.
    if keys_secret:
        container["envFrom"] = [{"secretRef": {"name": keys_secret}}]
    return [{"op": "add", "path": "/spec/containers/-", "value": container}]

@webhook_router.post("/mutate", dependencies=[Depends(verify_webhook_token)])
async def mutate_webhook(request: Request) -> JSONResponse:
    try:
        admission_review = await request.json()
        req = admission_review.get("request", {})
        uid = req.get("uid")
        obj = req.get("object", {})

        metadata = obj.get("metadata", {})
        labels = metadata.get("labels", {})
        containers = obj.get("spec", {}).get("containers", [])

        if labels.get("llm-shield.io/inject") == "true":
            # Avoid returning an invalid duplicate-name patch. This does not
            # validate that an existing same-name container has the desired image.
            if any(container.get("name") == "llm-shield-proxy" for container in containers):
                return JSONResponse({
                    "apiVersion": "admission.k8s.io/v1",
                    "kind": "AdmissionReview",
                    "response": {
                        "uid": uid,
                        "allowed": True
                    }
                })

            keys_secret = _keys_secret_for(metadata.get("annotations") or {})
            if keys_secret is None:
                logger.warning(
                    "Injected sidecar has no keys Secret; it will reject every request. Set "
                    "K8S_SIDECAR_SECRET_NAME or the pod annotation %s.",
                    KEYS_SECRET_ANNOTATION,
                )
            patch = _build_sidecar_patch(keys_secret)
            patch_b64 = base64.b64encode(json.dumps(patch).encode("utf-8")).decode("utf-8")

            return JSONResponse({
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": uid,
                    "allowed": True,
                    "patchType": "JSONPatch",
                    "patch": patch_b64
                }
            })

        return JSONResponse({
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": uid,
                "allowed": True
            }
        })
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
