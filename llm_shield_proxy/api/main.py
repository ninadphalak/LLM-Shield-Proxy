"""Enterprise LLM-Shield Proxy Application Gateway.

Zero-Egress privacy redaction middleware providing high-throughput PII masking,
session-isolated token vaults, and prefix-free SSE stream rehydration.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == 'win32':
    asyncio.WindowsSelectorEventLoopPolicy = asyncio.WindowsProactorEventLoopPolicy

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from llm_shield_proxy.adapters.anthropic_adapter import AnthropicAdapter
from llm_shield_proxy.adapters.provider_factory import resolve_provider
from llm_shield_proxy.api.health import health_router
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import vault_store
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.observability.metrics import (
    llm_shield_latency_seconds_bucket,
    llm_shield_requests_total,
    llm_shield_sse_active_streams,
)
from llm_shield_proxy.observability.tracing import propagator, tracer
from llm_shield_proxy.security.circuit_breaker import CircuitBreakerTrippedException, check_circuit_breaker
from llm_shield_proxy.security.watermark import generate_watermark_text
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.20"

class AppState:
    is_draining: bool = False
    active_requests: int = 0
    shutdown_event = asyncio.Event()

app_state = AppState()


@lru_cache(maxsize=1024)
def get_virtual_key_id(client_auth: str) -> str:
    """Computes a cryptographically salted virtual key fingerprint.

    Cached via LRU to guarantee 0ms latency impact during proxy routing.
    """
    return hashlib.pbkdf2_hmac("sha256", client_auth.encode("utf-8"), b"llm_shield_salt", 600000).hex()[:12]


class ConfigHandler(FileSystemEventHandler):
    """File watcher handler triggering dynamic configuration reloading."""

    def on_modified(self, event: Any) -> None:
        if event.src_path.endswith("config.yaml") or event.src_path.endswith(".env"):
            settings.reload()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application lifecycle, shared HTTP connection pools, and background observers."""
    import os

    from llm_shield_proxy.api.grpc_service import serve_ext_proc
    from llm_shield_proxy.security.fips_kat import run_fips_kat_self_test
    from llm_shield_proxy.security.vault_client import vault_provider

    if not run_fips_kat_self_test():
        if settings.FIPS_STRICT_MODE:
            AuditLogger.audit_logger.critical("FIPS 140-3 Cryptographic Integrity Self-Test Failed. Halting.")
            raise RuntimeError("FIPS 140-3 Cryptographic Integrity Self-Test Failed")
        else:
            AuditLogger.audit_logger.critical("FIPS 140-3 KAT Failed, but FIPS_STRICT_MODE=False. Continuing.")

    AuditLogger.log_startup_event()

    from llm_shield_proxy.engines.pii_engine import pii_engine
    print(
        f"--- LLM-Shield Proxy Startup Diagnostics ---\n"
        f"  PII Engine: {'Active (Tier 1, 2, 3)' if pii_engine.enable_tier3 else 'Active (Tier 1, 2)'}\n"
        f"  FIPS 140-3 Self-Test: {'Enforced (Passed)' if settings.FIPS_STRICT_MODE else 'Permissive'}\n"
        f"  Vault Provider: {'Enabled' if settings.ENABLE_VAULT_SECRETS else 'Disabled'}\n"
        f"  Rate Limiter: {'Redis' if settings.REDIS_URL else 'In-Memory'}\n"
        f"  Failure Mode: {settings.SHIELD_FAILURE_MODE}\n"
        f"--------------------------------------------"
    )
    if settings.ENABLE_VAULT_SECRETS:
        try:
            await vault_provider.fetch_secrets()
            vault_provider.start_background_refresh()
        except Exception as e:
            logger.error(f"Failed to fetch initial secrets from Vault: {e}")
            raise RuntimeError(f"Vault initialization failed: {e}")

    verify = settings.SSL_CA_BUNDLE_PATH if settings.ENABLE_MTLS and settings.SSL_CA_BUNDLE_PATH else True
    cert = None
    if settings.ENABLE_MTLS and settings.SSL_CLIENT_CERT_PATH and settings.SSL_CLIENT_KEY_PATH:
        cert = (settings.SSL_CLIENT_CERT_PATH, settings.SSL_CLIENT_KEY_PATH)

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
        verify=verify,
        cert=cert,
    )

    observer = Observer()
    observer.schedule(ConfigHandler(), path=".", recursive=False)
    observer.start()

    # Start gRPC ext_proc server in background
    sock_path = settings.EXT_PROC_SOCK_PATH

    if os.name != "nt":
        sock_dir = os.path.dirname(sock_path)
        # SECURITY: Ensure the parent directory is restricted to proxy/envoy group
        os.makedirs(sock_dir, exist_ok=True)
        os.chmod(sock_dir, 0o770)  # nosec B103

        if os.path.exists(sock_path):
            os.unlink(sock_path)

        # SECURITY: Prevent local privilege escalation (TOCTOU) by using umask
        # before socket creation, rather than chmod after creation.
        old_umask = os.umask(0o117) # Inverts to 0o660
        try:
            grpc_server = await serve_ext_proc(sock_path)
        finally:
            os.umask(old_umask)
    else:
        grpc_server = await serve_ext_proc(sock_path)

    yield

    app_state.is_draining = True
    if app_state.active_requests > 0:
        try:
            await asyncio.wait_for(app_state.shutdown_event.wait(), timeout=settings.DRAIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error("Draining timeout reached. Forcefully shutting down.")

    observer.stop()
    observer.join()
    await app.state.http_client.aclose()

    from llm_shield_proxy.security.vault_client import vault_provider
    await vault_provider.aclose()

    # Cleanly close gRPC server
    grpc_server.close()
    await grpc_server.wait_closed()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


app = FastAPI(
    title="LLM-Shield Proxy",
    description="Enterprise Zero-Egress Privacy Redaction Middleware Proxy",
    version=APP_VERSION,
    lifespan=lifespan,
)


app.include_router(health_router)


@app.middleware("http")
async def security_and_tracing_middleware(request: Request, call_next: Any) -> Response:
    """Attaches correlation request IDs and enterprise HTTP security headers."""
    if app_state.is_draining:
        return JSONResponse(status_code=503, content={"error": {"message": "Service Unavailable: Pod Draining", "type": "server_error"}})

    app_state.active_requests += 1
    try:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    finally:
        app_state.active_requests -= 1
        if app_state.is_draining and app_state.active_requests == 0:
            app_state.shutdown_event.set()


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
        verify = settings.SSL_CA_BUNDLE_PATH if settings.ENABLE_MTLS and settings.SSL_CA_BUNDLE_PATH else True
        cert = None
        if settings.ENABLE_MTLS and settings.SSL_CLIENT_CERT_PATH and settings.SSL_CLIENT_KEY_PATH:
            cert = (settings.SSL_CLIENT_CERT_PATH, settings.SSL_CLIENT_KEY_PATH)

        client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            http2=False,
            verify=verify,
            cert=cert,
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
# Observability Endpoints
# -----------------------------------------------------------------------------


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
    from llm_shield_proxy.security.vault_client import vault_provider

    attr_name = PROVIDER_KEY_MAP.get(hostname)
    if attr_name:
        if settings.ENABLE_VAULT_SECRETS:
            key = vault_provider.get_secret(attr_name)
            if key:
                return key
        key = getattr(settings, attr_name, None)
        if key:
            return key

    if settings.ENABLE_VAULT_SECRETS:
        key = vault_provider.get_secret("UPSTREAM_API_KEY")
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
    x_shield_masking_mode: Optional[str] = Header(None, alias="X-Shield-Masking-Mode"),
    x_shield_bypass_breaker: Optional[str] = Header(None, alias="X-Shield-Bypass-Breaker"),
) -> Response:
    """Main reverse-proxy catch-all endpoint handling redaction and rehydration."""
    start_time = time.perf_counter()
    ctx = propagator.extract(request.headers)
    with tracer.start_as_current_span("proxy_catch_all", context=ctx):
        try:
            response = await _proxy_catch_all_internal(request, path, x_session_id, x_upstream_base_url, x_shield_masking_mode, x_shield_bypass_breaker)
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
    x_shield_masking_mode: Optional[str] = None,
    x_shield_bypass_breaker: Optional[str] = None,
) -> Response:
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
    upstream_host_header = None
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
                # Overwrite upstream base with the resolved IP to prevent DNS rebinding SSRF
                port_str = f":{parsed.port}" if parsed.port else ""
                upstream_base = f"{parsed.scheme}://{ip}{port_str}{parsed.path}"
                upstream_host_header = hostname
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

    if upstream_host_header:
        headers["host"] = upstream_host_header

    # Inject trace context into upstream HTTP request headers
    propagator.inject(headers)

    # Extract client authorization
    client_auth = headers.get("authorization", "").replace("Bearer ", "").strip()
    if not client_auth:
        client_auth = headers.get("x-api-key", "").strip()
    if not client_auth:
        client_auth = headers.get("x-goog-api-key", "").strip()

    valid_keys = settings.valid_virtual_keys_set
    is_virtual_key = False
    virtual_key_id = "BYOK"

    matched_key = None
    if valid_keys:
        for vk in valid_keys:
            if hmac.compare_digest(client_auth, vk):
                matched_key = vk
                break

    if matched_key:
        is_virtual_key = True
        virtual_key_id = get_virtual_key_id(matched_key)
    elif client_auth.startswith(("sk-proj-", "sk-ant-", "AIza")):
        # Direct genuine BYOK provider key passthrough
        is_virtual_key = False
    elif settings.OVERRIDE_CLIENT_AUTH:
        # Bypass strict prefix checks if enterprise secret injection is active
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
    elif settings.OVERRIDE_CLIENT_AUTH and settings.UPSTREAM_API_KEY:
        headers["authorization"] = f"Bearer {settings.UPSTREAM_API_KEY}"
        headers.pop("x-api-key", None)
        headers.pop("x-goog-api-key", None)
        headers.pop("api-key", None)

    headers.pop("x-virtual-key-id", None)
    request_id = getattr(request.state, "request_id", None)
    request.state.virtual_key_id = virtual_key_id

    from llm_shield_proxy.security.rate_limit import rate_limiter
    if not await rate_limiter.acquire(virtual_key_id):
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
            headers={"Retry-After": "1"}
        )

    # Vault resolution based on masking mode
    from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault
    from llm_shield_proxy.engines.masking import MaskingMode, resolve_masking_mode

    masking_mode = resolve_masking_mode(x_shield_masking_mode)

    if masking_mode == MaskingMode.STATELESS_CRYPTO:
        vault = StatelessCryptoVault()
    elif masking_mode == MaskingMode.SCRUB:
        class ScrubVault:
            def __init__(self) -> None:
                self.type_counters: dict[str, int] = {}
            def get_or_create_token(self, original_val: str, entity_type: str) -> str:
                self.type_counters[entity_type] = self.type_counters.get(entity_type, 0) + 1
                return "[REDACTED]"
            def rehydrate(self, text: str, retention_length: int = 0) -> str:
                return text
        vault = ScrubVault() # type: ignore
    elif masking_mode == MaskingMode.STRUCTURAL_TAG:
        vault = await vault_store.get_vault_async(x_session_id, virtual_key_id)
        vault.synthetic = False
    else: # SYNTHETIC
        vault = await vault_store.get_vault_async(x_session_id, virtual_key_id)
        vault.synthetic = True

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
                            "message": f"Request payload exceeds maximum allowed limit of {settings.MAX_PAYLOAD_SIZE_BYTES // (1024 * 1024)}MB",
                            "type": "invalid_request_error",
                        }
                    },
                )
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
            )
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "Malformed JSON payload", "type": "invalid_request_error"}},
            )

        if isinstance(payload, dict):
            # Circuit Breaker Logic
            if settings.ENABLE_AGENT_BREAKER and x_session_id:
                bypass_breaker = str(x_shield_bypass_breaker).lower() in ("true", "1", "yes")
                if not bypass_breaker:
                    try:
                        await check_circuit_breaker(x_session_id, payload)
                    except CircuitBreakerTrippedException as cbe:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": "circuit_breaker_tripped",
                                "reason": "agent_loop_detected",
                                "consecutive_turns": cbe.consecutive_turns
                            },
                            headers={
                                "X-Shield-Circuit-Breaker": "TRIPPED",
                                "Retry-After": "60"
                            }
                        )

            # Pre-flight Watermark Check
            watermark_text = ""
            if settings.ENABLE_WATERMARKING and settings.SHIELD_WATERMARK_SECRET:
                response_format = payload.get("response_format", {})
                fmt_type = response_format.get("type", "") if isinstance(response_format, dict) else ""
                if "json" not in fmt_type.lower():
                    watermark_text = generate_watermark_text(
                        secret=settings.SHIELD_WATERMARK_SECRET,
                        authorization_header=request.headers.get("authorization"),
                        x_virtual_key_header=request.headers.get("x-virtual-key"),
                        client_ip=request.client.host if request.client else None,
                        session_id=x_session_id or "unknown_session"
                    )

            is_streaming = bool(payload.get("stream", False))
            try:
                active_profile = pii_engine.get_profile(virtual_key_id)
                loop = asyncio.get_running_loop()
                redacted_payload = await loop.run_in_executor(None, pii_engine.redact_payload, payload, vault, active_profile)

                # target_provider is refined with payload
                target_provider = resolve_provider(dict(request.headers), redacted_payload)
                if target_provider == "anthropic":
                    anthropic_payload = AnthropicAdapter.transform_request(redacted_payload)
                    redacted_bytes = json.dumps(anthropic_payload).encode("utf-8")
                    target_url = "https://api.anthropic.com/v1/messages"
                    headers["anthropic-version"] = settings.ANTHROPIC_API_VERSION
                    headers["x-api-key"] = headers.get("authorization", "").replace("Bearer ", "").strip()
                    headers.pop("authorization", None)
                else:
                    redacted_bytes = json.dumps(redacted_payload).encode("utf-8")
            except ValueError as ve:
                if str(ve) == "Maximum payload nesting depth exceeded":
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": {
                                "message": "Payload nesting depth exceeded maximum limit (JSON bomb protection)",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                raise ve
            except Exception as e:
                if settings.SHIELD_FAILURE_MODE == "FAIL_CLOSED":
                    logger.error(f"PII Engine failure (FAIL_CLOSED): {e}")
                    return JSONResponse(
                        status_code=503,
                        content={"error": {"message": "DLP Inspection Failure: Request blocked by security policy", "type": "dlp_failure"}},
                    )
                else:
                    logger.error(f"PII Engine failure (FAIL_OPEN): {e}")
                    redacted_bytes = body_bytes

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
                        async for chunk in rehydrate_sse_stream(upstream_res.aiter_bytes(), vault, watermark_text=watermark_text):
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
                    with open("exception_log.txt", "w") as f:
                        f.write(repr(err))
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
                    loop = asyncio.get_running_loop()

                    if target_provider == "anthropic":
                        rehydrated_res = await loop.run_in_executor(None, _rehydrate_json_response, res_json, vault)
                        rehydrated_res = AnthropicAdapter.transform_response(rehydrated_res)
                    else:
                        rehydrated_res = await loop.run_in_executor(None, _rehydrate_json_response, res_json, vault)

                    if watermark_text:
                        if "choices" in rehydrated_res and isinstance(rehydrated_res["choices"], list) and rehydrated_res["choices"]:
                            msg = rehydrated_res["choices"][0].get("message", {})
                            if isinstance(msg, dict) and "content" in msg and isinstance(msg["content"], str):
                                msg["content"] += watermark_text
                        elif "content" in rehydrated_res and isinstance(rehydrated_res["content"], list) and rehydrated_res["content"]:
                            block = rehydrated_res["content"][0]
                            if isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
                                block["text"] += watermark_text

                    return JSONResponse(
                        content=rehydrated_res,
                        status_code=upstream_res.status_code,
                        headers=res_headers,
                    )
                except Exception:
                    loop = asyncio.get_running_loop()
                    rehydrated_text = await loop.run_in_executor(None, vault.rehydrate, upstream_res.text)
                    return Response(
                        content=rehydrated_text,
                        status_code=upstream_res.status_code,
                        headers=res_headers,
                    )

    # Pass-through for non-POST requests
    AuditLogger.log_proxy_event(x_session_id, path, request.method, virtual_key_id, 200, request_id)
    try:
        body_bytes = await read_body_with_limit(request)
    except ValueError as ve:
        if str(ve) == "Payload Too Large":
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "message": f"Request payload exceeds maximum allowed limit of {settings.MAX_PAYLOAD_SIZE_BYTES // (1024 * 1024)}MB",
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

    if upstream_res.status_code >= 400:
        # Prevent upstream auth key leakage
        return JSONResponse(
            status_code=upstream_res.status_code,
            content={
                "error": {
                    "message": "Failed to communicate with upstream provider.",
                    "type": "upstream_error",
                    "code": upstream_res.status_code,
                }
            },
        )

    return Response(
        content=upstream_res.content,
        status_code=upstream_res.status_code,
        headers=dict(upstream_res.headers),
    )


def _rehydrate_json_response(res_json: Dict[str, Any], vault: Any) -> Dict[str, Any]:
    """Recursively walks choices, messages, tool calls, and content blocks to rehydrate tokens."""
    if not isinstance(res_json, dict):
        return res_json

    import copy

    res_copy = copy.deepcopy(res_json)

    # 1. OpenAI Chat Completion choices
    if "choices" in res_copy and isinstance(res_copy["choices"], list):
        for choice in res_copy["choices"]:
            if isinstance(choice, dict):
                message = choice.get("message", {})
                if isinstance(message, dict):
                    if "content" in message and isinstance(message["content"], str):
                        message["content"] = vault.rehydrate(message["content"])

                    # Rehydrate tool_calls function arguments if generated by LLM
                    if "tool_calls" in message and isinstance(message["tool_calls"], list):
                        for tc in message["tool_calls"]:
                            if isinstance(tc, dict) and "function" in tc and isinstance(tc["function"], dict):
                                fn = tc["function"]
                                if "arguments" in fn and isinstance(fn["arguments"], str):
                                    fn["arguments"] = vault.rehydrate(fn["arguments"])

                    # Rehydrate legacy function_call arguments
                    if "function_call" in message and isinstance(message["function_call"], dict):
                        fn = message["function_call"]
                        if "arguments" in fn and isinstance(fn["arguments"], str):
                            fn["arguments"] = vault.rehydrate(fn["arguments"])

                delta = choice.get("delta", {})
                if isinstance(delta, dict) and "content" in delta and isinstance(delta["content"], str):
                    delta["content"] = vault.rehydrate(delta["content"])

    # 2. Anthropic Claude top-level content blocks
    if "content" in res_copy and isinstance(res_copy["content"], list):
        for block in res_copy["content"]:
            if isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
                block["text"] = vault.rehydrate(block["text"])

    return res_copy
