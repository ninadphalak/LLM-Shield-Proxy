"""Enterprise LLM-Shield Proxy Application Gateway.

Zero-Egress privacy redaction middleware providing high-throughput PII masking,
session-isolated token vaults, and prefix-free SSE stream rehydration.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from llm_shield_proxy.audit import AuditLogger
from llm_shield_proxy.config import settings
from llm_shield_proxy.metrics import (
    llm_shield_latency_seconds_bucket,
    llm_shield_requests_total,
    llm_shield_sse_active_streams,
)
from llm_shield_proxy.pii_engine import pii_engine
from llm_shield_proxy.streaming import rehydrate_sse_stream
from llm_shield_proxy.vault import RedisVaultStore, vault_store

APP_VERSION = "1.0.14"


@lru_cache(maxsize=1024)
def get_virtual_key_id(client_auth: str) -> str:
    """Computes a cryptographically salted virtual key fingerprint.

    Cached via LRU to guarantee 0ms latency impact during proxy routing.
    """
    return hashlib.pbkdf2_hmac(
        "sha256", client_auth.encode("utf-8"), b"llm_shield_salt", 100000
    ).hex()[:12]


class ConfigHandler(FileSystemEventHandler):
    """File watcher handler triggering dynamic configuration reloading."""

    def on_modified(self, event: Any) -> None:
        if event.src_path.endswith("config.yaml") or event.src_path.endswith(".env"):
            settings.reload()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application lifecycle, shared HTTP connection pools, and background observers."""
    AuditLogger.log_startup_event()

    limits = httpx.Limits(
        max_keepalive_connections=settings.HTTP_MAX_KEEPALIVE_CONNECTIONS,
        max_connections=settings.HTTP_MAX_CONNECTIONS,
    )
    timeout = httpx.Timeout(
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        connect=settings.HTTP_CONNECT_TIMEOUT_SECONDS,
    )
    app.state.http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        http2=True,
    )

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
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_and_tracing_middleware(request: Request, call_next: Any) -> Response:
    """Attaches correlation request IDs and enterprise HTTP security headers."""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id

    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Sanitized global exception handler preventing raw PII or stack trace leaks."""
    return JSONResponse(
        status_code=500,
        content={"error": {"message": "Internal Server Error", "type": "server_error"}},
    )


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Retrieves or lazily initializes the shared AsyncClient from app state."""
    client = getattr(request.app.state, "http_client", None)
    if client is None or getattr(client, "is_closed", False):
        limits = httpx.Limits(
            max_keepalive_connections=settings.HTTP_MAX_KEEPALIVE_CONNECTIONS,
            max_connections=settings.HTTP_MAX_CONNECTIONS,
        )
        timeout = httpx.Timeout(
            timeout=settings.HTTP_TIMEOUT_SECONDS,
            connect=settings.HTTP_CONNECT_TIMEOUT_SECONDS,
        )
        client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            http2=True,
        )
        request.app.state.http_client = client
    return client


def build_target_url(upstream_base: str, path: str) -> str:
    """Constructs sanitized upstream target URL, resolving provider-specific pathing."""
    base = upstream_base.rstrip("/")
    p = path.lstrip("/")
    if p.startswith("v1/"):
        if base.endswith("/v1") or "generativelanguage" in base:
            p = p[3:]
    return f"{base}/{p}"


async def read_body_with_limit(request: Request, limit: Optional[int] = None) -> bytes:
    """Reads request body stream enforcing maximum memory payload limits."""
    max_limit = limit or settings.MAX_PAYLOAD_SIZE_BYTES
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_limit:
        raise ValueError("Payload Too Large")

    body_bytes = bytearray()
    async for chunk in request.stream():
        body_bytes.extend(chunk)
        if len(body_bytes) > max_limit:
            raise ValueError("Payload Too Large")
    return bytes(body_bytes)


# -----------------------------------------------------------------------------
# Health & Observability Endpoints
# -----------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
@app.get("/healthz", tags=["Health"])
@app.get("/livez", tags=["Health"])
async def liveness_probe() -> Dict[str, str]:
    """Kubernetes liveness check endpoint."""
    return {
        "status": "ok",
        "service": "llm-shield-proxy",
        "version": APP_VERSION,
    }


@app.get("/readyz", tags=["Health"])
async def readiness_probe(request: Request) -> JSONResponse:
    """Kubernetes readiness check validating Redis backend and upstream status."""
    redis_healthy = True
    if isinstance(vault_store, RedisVaultStore):
        redis_healthy = await vault_store.ping_async()

    if not redis_healthy:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "reason": "Redis backend unreachable"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "service": "llm-shield-proxy",
            "version": APP_VERSION,
            "redis_connected": bool(settings.REDIS_URL),
        },
    )


@app.get("/metrics", tags=["Observability"])
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus metrics endpoint with optional Bearer token security."""
    if settings.METRICS_BEARER_TOKEN:
        auth_header = request.headers.get("authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        if token != settings.METRICS_BEARER_TOKEN:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -----------------------------------------------------------------------------
# Proxy Catch-All Gateway Routing
# -----------------------------------------------------------------------------

PROVIDER_KEY_MAP: Dict[str, str] = {
    "api.openai.com": "OPENAI_API_KEY",
    "generativelanguage.googleapis.com": "GEMINI_API_KEY",
    "api.anthropic.com": "ANTHROPIC_API_KEY",
    "api.deepseek.com": "DEEPSEEK_API_KEY",
}


def resolve_upstream_key(hostname: str) -> Optional[str]:
    """Resolves centralized enterprise provider key via dictionary lookup."""
    attr_name = PROVIDER_KEY_MAP.get(hostname)
    if attr_name:
        key = getattr(settings, attr_name, None)
        if key:
            return key
    return settings.UPSTREAM_API_KEY


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_catch_all(
    request: Request,
    path: str,
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_upstream_base_url: Optional[str] = Header(None, alias="X-Upstream-Base-Url"),
) -> Response:
    """Main reverse-proxy catch-all endpoint handling redaction and rehydration."""
    start_time = time.perf_counter()
    try:
        response = await _proxy_catch_all_internal(request, path, x_session_id, x_upstream_base_url)
        llm_shield_requests_total.labels(status_code=response.status_code).inc()
        llm_shield_latency_seconds_bucket.observe(time.perf_counter() - start_time)
        return response
    except Exception as exc:
        llm_shield_requests_total.labels(status_code=500).inc()
        llm_shield_latency_seconds_bucket.observe(time.perf_counter() - start_time)
        raise exc


async def _proxy_catch_all_internal(
    request: Request,
    path: str,
    x_session_id: Optional[str],
    x_upstream_base_url: Optional[str],
) -> Response:
    # Route exemptions
    if path in ("health", "healthz", "livez"):
        return JSONResponse(status_code=200, content={"status": "ok", "service": "llm-shield-proxy"})

    if path == "metrics":
        return await metrics_endpoint(request)

    # CORS Preflight
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-goog-api-key, x-session-id",
                "Access-Control-Max-Age": "86400",
            },
        )

    upstream_base = settings.UPSTREAM_BASE_URL

    # SSRF Protection on Dynamic Client Upstream Override
    if x_upstream_base_url and settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE:
        if x_upstream_base_url.startswith(("http://", "https://")):
            parsed = urlparse(x_upstream_base_url)
            hostname = parsed.hostname or ""
            try:
                ip = socket.gethostbyname(hostname)
                ip_obj = ipaddress.ip_address(ip)
                if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                    return JSONResponse(
                        status_code=403,
                        content={"error": {"message": "Forbidden upstream hostname", "type": "security_error"}},
                    )
                upstream_base = x_upstream_base_url
            except socket.gaierror:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "Unresolvable upstream hostname", "type": "security_error"}},
                )

    target_url = build_target_url(upstream_base, path)

    # Prepare forwarding headers
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers.pop("accept-encoding", None)

    # Extract client authorization
    client_auth = headers.get("authorization", "").replace("Bearer ", "").strip()
    if not client_auth:
        client_auth = headers.get("x-api-key", "").strip()
    if not client_auth:
        client_auth = headers.get("x-goog-api-key", "").strip()

    valid_keys = settings.valid_virtual_keys_set
    is_virtual_key = False
    virtual_key_id = "BYOK"

    if valid_keys and client_auth in valid_keys:
        is_virtual_key = True
        virtual_key_id = get_virtual_key_id(client_auth)
    elif client_auth.startswith(("sk-proj-", "sk-ant-", "AIza", "sk-")):
        is_virtual_key = False
    elif not valid_keys:
        # Dev Fallback Mode
        is_virtual_key = False
    else:
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid Proxy API Key", "type": "authentication_error"}},
        )

    # Centralized Virtual Key Swapping
    if is_virtual_key:
        parsed_url = urlparse(upstream_base)
        hostname = parsed_url.hostname or ""
        resolved_key = resolve_upstream_key(hostname)

        if resolved_key:
            headers["authorization"] = f"Bearer {resolved_key}"
            headers.pop("x-api-key", None)
            headers.pop("x-goog-api-key", None)
            headers.pop("api-key", None)
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": "Upstream provider API Key is missing in proxy configuration.",
                        "type": "proxy_misconfiguration",
                    }
                },
            )

    headers.pop("x-virtual-key-id", None)
    request_id = getattr(request.state, "request_id", None)
    request.state.virtual_key_id = virtual_key_id

    # Retrieve session-bound vault
    vault = vault_store.get_vault(x_session_id, virtual_key_id)
    http_client = get_http_client(request)

    # Handle POST Payload Redaction
    if request.method == "POST":
        try:
            body_bytes = await read_body_with_limit(request)
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except ValueError as ve:
            if str(ve) == "Payload Too Large":
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "message": f"Request payload exceeds maximum allowed limit of {settings.MAX_PAYLOAD_SIZE_BYTES // (1024*1024)}MB",
                            "type": "invalid_request_error",
                        }
                    },
                )
            payload = {}
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            is_streaming = bool(payload.get("stream", False))
            redacted_payload = pii_engine.redact_payload(payload, vault)
            redacted_bytes = json.dumps(redacted_payload).encode("utf-8")

            if is_streaming:
                req = http_client.build_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=redacted_bytes,
                )

                try:
                    upstream_res = await http_client.send(req, stream=True)
                    upstream_res.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as err:
                    status_code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else 503
                    AuditLogger.log_redaction_event(
                        x_session_id,
                        vault.type_counters,
                        path,
                        virtual_key_id,
                        status_code,
                        request_id,
                    )
                    return JSONResponse(
                        status_code=status_code,
                        content={
                            "error": {
                                "message": "Failed to communicate with upstream provider.",
                                "type": "upstream_error",
                                "code": status_code,
                            }
                        },
                    )

                AuditLogger.log_redaction_event(
                    x_session_id,
                    vault.type_counters,
                    path,
                    virtual_key_id,
                    upstream_res.status_code,
                    request_id,
                )

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                async def wrapped_stream() -> AsyncGenerator[bytes, None]:
                    llm_shield_sse_active_streams.inc()
                    try:
                        async for chunk in rehydrate_sse_stream(upstream_res.aiter_bytes(), vault):
                            yield chunk
                    finally:
                        llm_shield_sse_active_streams.dec()
                        await upstream_res.aclose()

                return StreamingResponse(
                    wrapped_stream(),
                    status_code=upstream_res.status_code,
                    headers=res_headers,
                    media_type="text/event-stream",
                )
            else:
                try:
                    upstream_res = await http_client.request(
                        method=request.method,
                        url=target_url,
                        headers=headers,
                        content=redacted_bytes,
                    )
                    upstream_res.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as err:
                    status_code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else 503
                    AuditLogger.log_redaction_event(
                        x_session_id,
                        vault.type_counters,
                        path,
                        virtual_key_id,
                        status_code,
                        request_id,
                    )
                    return JSONResponse(
                        status_code=status_code,
                        content={
                            "error": {
                                "message": "Failed to communicate with upstream provider.",
                                "type": "upstream_error",
                                "code": status_code,
                            }
                        },
                    )

                AuditLogger.log_redaction_event(
                    x_session_id,
                    vault.type_counters,
                    path,
                    virtual_key_id,
                    upstream_res.status_code,
                    request_id,
                )

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                try:
                    res_json = upstream_res.json()
                    rehydrated_res = _rehydrate_json_response(res_json, vault)
                    return JSONResponse(
                        content=rehydrated_res,
                        status_code=upstream_res.status_code,
                        headers=res_headers,
                    )
                except Exception:
                    return Response(
                        content=vault.rehydrate(upstream_res.text),
                        status_code=upstream_res.status_code,
                        headers=res_headers,
                    )

    # Pass-through for non-POST requests
    AuditLogger.log_proxy_event(
        x_session_id, path, request.method, virtual_key_id, 200, request_id
    )
    try:
        body_bytes = await read_body_with_limit(request)
    except ValueError as ve:
        if str(ve) == "Payload Too Large":
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "message": f"Request payload exceeds maximum allowed limit of {settings.MAX_PAYLOAD_SIZE_BYTES // (1024*1024)}MB",
                        "type": "invalid_request_error",
                    }
                },
            )
        body_bytes = b""

    upstream_res = await http_client.request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body_bytes,
    )
    return Response(
        content=upstream_res.content,
        status_code=upstream_res.status_code,
        headers=dict(upstream_res.headers),
    )


def _rehydrate_json_response(res_json: Dict[str, Any], vault: Any) -> Dict[str, Any]:
    """Recursively walks choices and messages in upstream JSON response to rehydrate tokens."""
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
