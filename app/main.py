import json
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, Header
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import settings
from app.vault import vault_store
from app.pii_engine import pii_engine
from app.streaming import rehydrate_sse_stream
from app.audit import AuditLogger


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=120.0)
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="LLM-Shield Proxy",
    description="Enterprise Zero-Egress Privacy Redaction Middleware Proxy",
    version="1.0.0",
    lifespan=lifespan
)


def get_http_client(request: Request) -> httpx.AsyncClient:
    if not hasattr(request.app.state, "http_client") or request.app.state.http_client is None:
        request.app.state.http_client = httpx.AsyncClient(timeout=120.0)
    return request.app.state.http_client


def build_target_url(upstream_base: str, path: str) -> str:
    base = upstream_base.rstrip("/")
    p = path.lstrip("/")
    if base.endswith("/v1") and p.startswith("v1/"):
        p = p[3:]
    return f"{base}/{p}"


@app.get("/health", tags=["Health"])
@app.get("/livez", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "service": "llm-shield-proxy",
        "version": "1.0.2"
    }


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_catch_all(
    request: Request,
    path: str,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_upstream_base_url: Optional[str] = Header(None, alias="X-Upstream-Base-Url")
):
    upstream_base = x_upstream_base_url or settings.UPSTREAM_BASE_URL
    target_url = build_target_url(upstream_base, path)


    # Prepare forwarding headers (strip hop-by-hop and compression headers)
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers.pop("accept-encoding", None)

    if settings.OPENAI_API_KEY:
        headers["authorization"] = f"Bearer {settings.OPENAI_API_KEY}"

    vault = vault_store.get_vault(x_session_id)
    http_client: httpx.AsyncClient = get_http_client(request)

    if request.method == "POST":
        try:
            body_bytes = await request.body()
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            is_streaming = payload.get("stream", False)
            redacted_payload = pii_engine.redact_payload(payload, vault)
            redacted_bytes = json.dumps(redacted_payload).encode("utf-8")

            AuditLogger.log_redaction_event(x_session_id, vault.type_counters, path)

            if is_streaming:
                req = http_client.build_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=redacted_bytes
                )
                upstream_res = await http_client.send(req, stream=True)

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                if upstream_res.status_code >= 400:
                    err_content = await upstream_res.aread()
                    await upstream_res.aclose()
                    return Response(
                        content=err_content,
                        status_code=upstream_res.status_code,
                        headers=res_headers,
                        media_type=res_headers.get("content-type", "application/json")
                    )

                return StreamingResponse(
                    rehydrate_sse_stream(upstream_res.aiter_bytes(), vault),
                    status_code=upstream_res.status_code,
                    headers=res_headers,
                    media_type="text/event-stream"
                )
            else:
                upstream_res = await http_client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=redacted_bytes
                )
                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                try:
                    res_json = upstream_res.json()
                    rehydrated_res = _rehydrate_json_response(res_json, vault)
                    return JSONResponse(content=rehydrated_res, status_code=upstream_res.status_code, headers=res_headers)
                except Exception:
                    return Response(
                        content=vault.rehydrate(upstream_res.text),
                        status_code=upstream_res.status_code,
                        headers=res_headers
                    )


    # For non-POST or pass-through requests
    AuditLogger.log_proxy_event(x_session_id, path, request.method)
    body_bytes = await request.body()
    upstream_res = await http_client.request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body_bytes
    )
    return Response(
        content=upstream_res.content,
        status_code=upstream_res.status_code,
        headers=dict(upstream_res.headers)
    )


def _rehydrate_json_response(res_json: dict, vault) -> dict:
    if not isinstance(res_json, dict):
        return res_json

    res_copy = res_json.copy()
    if "choices" in res_copy and isinstance(res_copy["choices"], list):
        for choice in res_copy["choices"]:
            if isinstance(choice, dict):
                message = choice.get("message", {})
                if isinstance(message, dict) and "content" in message and isinstance(message["content"], str):
                    message["content"] = vault.rehydrate(message["content"])
                delta = choice.get("delta", {})
                if isinstance(delta, dict) and "content" in delta and isinstance(delta["content"], str):
                    delta["content"] = vault.rehydrate(delta["content"])

    return res_copy
