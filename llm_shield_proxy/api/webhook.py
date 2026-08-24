"""Zero-Dependency Kubernetes Mutating Admission Webhook.

Injects the LLM-Shield Proxy as a sidecar into Pods labeled with
llm-shield.io/inject: "true" using standard JSON Patch (RFC 6902).
Consumes 0MB of persistent memory by operating entirely within the FastAPI loop.
"""

import base64
import json
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

webhook_router = APIRouter(prefix="/v1/k8s", tags=["Kubernetes Webhook"])

def _build_sidecar_patch() -> list[Dict[str, Any]]:
    return [
        {
            "op": "add",
            "path": "/spec/containers/-",
            "value": {
                "name": "llm-shield-proxy",
                "image": "llm-shield/proxy:latest",
                "ports": [{"containerPort": 8000}],
                "env": [
                    {"name": "SHIELD_FAILURE_MODE", "value": "FAIL_CLOSED"},
                    {"name": "ENABLE_TIER3_ONNX_NER", "value": "false"},
                ],
                "resources": {
                    "limits": {"memory": "60Mi", "cpu": "200m"},
                    "requests": {"memory": "25Mi", "cpu": "50m"}
                }
            }
        }
    ]

@webhook_router.post("/mutate")
async def mutate_webhook(request: Request) -> JSONResponse:
    try:
        admission_review = await request.json()
        req = admission_review.get("request", {})
        uid = req.get("uid")
        obj = req.get("object", {})
        
        metadata = obj.get("metadata", {})
        labels = metadata.get("labels", {})
        
        if labels.get("llm-shield.io/inject") == "true":
            patch = _build_sidecar_patch()
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
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
