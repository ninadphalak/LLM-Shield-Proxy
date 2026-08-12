import json
import hashlib
from urllib.parse import urlparse
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
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from app.metrics import (
    llm_shield_requests_total,
    llm_shield_pii_redacted_total,
    llm_shield_sse_active_streams,
    llm_shield_latency_seconds_bucket
)
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
class ConfigHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith("config.yaml") or event.src_path.endswith(".env"):
            settings.reload()

@asynccontextmanager
async def lifespan(app: FastAPI):
    AuditLogger.log_startup_event()
    app.state.http_client = httpx.AsyncClient(timeout=120.0, http2=True, limits=httpx.Limits(max_keepalive_connections=100, max_connections=500))
    observer = Observer()
    observer.schedule(ConfigHandler(), path=".", recursive=False)
    observer.start()
    yield
    observer.stop()
    observer.join()
    await app.state.http_client.aclose()


app = FastAPI(
    title="LLM-Shield Proxy",
    description="Enterprise Zero-Egress Privacy Redaction Middleware Proxy",
    version="1.0.7",
    lifespan=lifespan
)


def get_http_client(request: Request) -> httpx.AsyncClient:
    if not hasattr(request.app.state, "http_client") or request.app.state.http_client is None:
        request.app.state.http_client = httpx.AsyncClient(timeout=120.0, http2=True, limits=httpx.Limits(max_keepalive_connections=100, max_connections=500))
    return request.app.state.http_client


def build_target_url(upstream_base: str, path: str) -> str:
    base = upstream_base.rstrip("/")
    p = path.lstrip("/")
    if p.startswith("v1/"):
        if base.endswith("/v1"):
            p = p[3:]  # avoid /v1/v1 for OpenAI
        elif "generativelanguage" in base:
            p = p[3:]  # Gemini OpenAI API endpoint doesn't use v1/
    return f"{base}/{p}"


async def read_body_with_limit(request: Request, limit: int = 10 * 1024 * 1024) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > limit:
        raise ValueError("Payload Too Large")
    
    body_bytes = bytearray()
    async for chunk in request.stream():
        body_bytes.extend(chunk)
        if len(body_bytes) > limit:
            raise ValueError("Payload Too Large")
    return bytes(body_bytes)


@app.get("/health", tags=["Health"])
@app.get("/livez", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "service": "llm-shield-proxy",
        "version": "1.0.4"
    }


import time

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_catch_all(
    request: Request,
    path: str,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_upstream_base_url: Optional[str] = Header(None, alias="X-Upstream-Base-Url")
):
    start_time = time.time()
    try:
        response = await _proxy_catch_all_internal(request, path, x_session_id, x_upstream_base_url)
        llm_shield_requests_total.labels(status_code=response.status_code).inc()
        llm_shield_latency_seconds_bucket.observe(time.time() - start_time)
        return response
    except Exception as e:
        llm_shield_requests_total.labels(status_code=500).inc()
        llm_shield_latency_seconds_bucket.observe(time.time() - start_time)
        raise e

async def _proxy_catch_all_internal(
    request: Request,
    path: str,
    x_session_id: Optional[str],
    x_upstream_base_url: Optional[str]
):
    upstream_base = settings.UPSTREAM_BASE_URL
    if x_upstream_base_url and settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE:
        if x_upstream_base_url.startswith("http://") or x_upstream_base_url.startswith("https://"):
            parsed = urlparse(x_upstream_base_url)
            hostname = parsed.hostname or ""
            if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254"):
                return JSONResponse(status_code=403, content={"error": {"message": "Forbidden upstream hostname", "type": "security_error"}})
            upstream_base = x_upstream_base_url

    target_url = build_target_url(upstream_base, path)

    # Route Exemptions for Health Probes
    if path in ("healthz", "livez"):
        return JSONResponse(status_code=200, content={"status": "ok", "service": "llm-shield-proxy"})

    if path == "metrics":
        if settings.METRICS_BEARER_TOKEN:
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer ") or auth_header.replace("Bearer ", "").strip() != settings.METRICS_BEARER_TOKEN:
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # CORS OPTIONS Preflight Exemption
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-goog-api-key, x-session-id",
                "Access-Control-Max-Age": "86400"
            }
        )


    # Prepare forwarding headers (strip hop-by-hop and compression headers)
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers.pop("accept-encoding", None)

    # Extract client key from various standard headers
    client_auth = headers.get("authorization", "").replace("Bearer ", "").strip()
    if not client_auth:
        client_auth = headers.get("x-api-key", "").strip()
    if not client_auth:
        client_auth = headers.get("x-goog-api-key", "").strip()

    # Determine if key is a Virtual Key or BYOK
    is_virtual_key = False
    is_byok = False
    virtual_key_id = "BYOK"

    valid_keys = settings.valid_virtual_keys_set
    if valid_keys:
        if client_auth in valid_keys:
            is_virtual_key = True
            virtual_key_id = hashlib.sha256(client_auth.encode()).hexdigest()[:12]
        elif client_auth.startswith("sk-proj-") or client_auth.startswith("sk-ant-") or client_auth.startswith("AIza"):
            is_byok = True
        else:
            # Missing or Invalid Key
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "Invalid Proxy API Key", "type": "authentication_error"}}
            )
    else:
        # Dev Fallback Mode
        if client_auth.startswith("sk-proxy-") or "dummy" in client_auth.lower() or "local" in client_auth.lower():
            is_virtual_key = True
            virtual_key_id = "dev-fallback"
        else:
            is_byok = True

    if is_virtual_key:
        # Dynamic Enterprise Key Routing (Centralized Keys)
        resolved_key = None
        parsed_url = urlparse(upstream_base)
        hostname = parsed_url.hostname or ""
        
        if hostname == "api.openai.com" and settings.OPENAI_API_KEY:
            resolved_key = settings.OPENAI_API_KEY
        elif hostname == "generativelanguage.googleapis.com" and settings.GEMINI_API_KEY:
            resolved_key = settings.GEMINI_API_KEY
        elif hostname == "api.anthropic.com" and settings.ANTHROPIC_API_KEY:
            resolved_key = settings.ANTHROPIC_API_KEY
        elif hostname == "api.deepseek.com" and settings.DEEPSEEK_API_KEY:
            resolved_key = settings.DEEPSEEK_API_KEY
        elif settings.UPSTREAM_API_KEY:
            resolved_key = settings.UPSTREAM_API_KEY
    
        if resolved_key:
            headers["authorization"] = f"Bearer {resolved_key}"
            headers.pop("x-api-key", None)
            headers.pop("x-goog-api-key", None)
        else:
            # Prevent leaking proxy keys upstream
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "Upstream provider API Key is missing in proxy configuration.", "type": "proxy_misconfiguration"}}
            )

    # Strip proxy-internal headers
    headers.pop("x-virtual-key-id", None)
    
    # Store virtual_key_id in request state for AuditLogger
    request.state.virtual_key_id = virtual_key_id

    vault = vault_store.get_vault(x_session_id, virtual_key_id)
    http_client: httpx.AsyncClient = get_http_client(request)

    if request.method == "POST":
        try:
            body_bytes = await read_body_with_limit(request)
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except ValueError as ve:
            if str(ve) == "Payload Too Large":
                return JSONResponse(status_code=413, content={"error": {"message": "Request payload exceeds maximum allowed limit of 10MB", "type": "invalid_request_error"}})
            payload = {}
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            is_streaming = payload.get("stream", False)
            redacted_payload = pii_engine.redact_payload(payload, vault)
            redacted_bytes = json.dumps(redacted_payload).encode("utf-8")

            if is_streaming:
                req = http_client.build_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=redacted_bytes
                )
                
                try:
                    upstream_res = await http_client.send(req, stream=True)
                    upstream_res.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    status_code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else 503
                    err_payload = {
                        "error": {
                            "message": "Failed to communicate with upstream provider.",
                            "type": "upstream_error",
                            "code": status_code
                        }
                    }
                    AuditLogger.log_redaction_event(x_session_id, vault.type_counters, path, request.state.virtual_key_id, status_code)
                    return JSONResponse(status_code=status_code, content=err_payload)

                AuditLogger.log_redaction_event(x_session_id, vault.type_counters, path, request.state.virtual_key_id, upstream_res.status_code)

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                async def wrapped_stream():
                    llm_shield_sse_active_streams.inc()
                    try:
                        async for chunk in rehydrate_sse_stream(upstream_res.aiter_bytes(), vault):
                            yield chunk
                    finally:
                        llm_shield_sse_active_streams.dec()

                return StreamingResponse(
                    wrapped_stream(),
                    status_code=upstream_res.status_code,
                    headers=res_headers,
                    media_type="text/event-stream"
                )
            else:
                try:
                    upstream_res = await http_client.request(
                        method=request.method,
                        url=target_url,
                        headers=headers,
                        content=redacted_bytes
                    )
                    upstream_res.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    status_code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else 503
                    err_payload = {
                        "error": {
                            "message": "Failed to communicate with upstream provider.",
                            "type": "upstream_error",
                            "code": status_code
                        }
                    }
                    AuditLogger.log_redaction_event(x_session_id, vault.type_counters, path, request.state.virtual_key_id, status_code)
                    return JSONResponse(status_code=status_code, content=err_payload)

                AuditLogger.log_redaction_event(x_session_id, vault.type_counters, path, request.state.virtual_key_id, upstream_res.status_code)
                
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
    AuditLogger.log_proxy_event(x_session_id, path, request.method, request.state.virtual_key_id)
    try:
        body_bytes = await read_body_with_limit(request)
    except ValueError as ve:
        if str(ve) == "Payload Too Large":
            return JSONResponse(status_code=413, content={"error": {"message": "Request payload exceeds maximum allowed limit of 10MB", "type": "invalid_request_error"}})
        body_bytes = b""
        
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
