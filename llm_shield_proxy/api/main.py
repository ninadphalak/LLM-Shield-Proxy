"""Enterprise LLM-Shield Proxy Application Gateway.

Zero-Egress privacy redaction middleware providing high-throughput PII masking,
session-isolated token vaults, and prefix-free SSE stream rehydration.
"""

from __future__ import annotations

import asyncio
import random
import sys
import threading

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # type: ignore

import datetime
import hashlib
import hmac
import ipaddress
import logging
import re
import socket
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
import orjson
from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from llm_shield_proxy.adapters.anthropic_adapter import AnthropicAdapter
from llm_shield_proxy.adapters.provider_factory import resolve_provider
from llm_shield_proxy.api.health import health_router
from llm_shield_proxy.api.webhook import webhook_router
from llm_shield_proxy.core.config import request_policy_ctx, settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.stateless_mutation_engine.ast_mutator import (
    ASTDepthExceededException,
    StatelessASTVisitor,
)
from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher
from llm_shield_proxy.engines.stateless_mutation_engine.schema_rewriter import DynamicSchemaRewriter
from llm_shield_proxy.engines.vault import vault_store
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.observability.metrics import (
    llm_shield_latency_seconds_bucket,
    llm_shield_requests_total,
    llm_shield_sse_active_streams,
)
from llm_shield_proxy.observability.telemetry_dispatcher import dispatch_telemetry
from llm_shield_proxy.observability.tracing import propagator, tracer
from llm_shield_proxy.security.circuit_breaker import CircuitBreakerTrippedException, check_circuit_breaker
from llm_shield_proxy.security.tool_rbac import (
    BasePolicyResolver,
    BoundedLockMap,
    InMemoryPolicyResolver,
    OPAPolicyResolver,
    RedisPolicyResolver,
    VaultPolicyResolver,
)
from llm_shield_proxy.security.watermark import generate_watermark_text
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

logger = logging.getLogger(__name__)

APP_VERSION = "1.2.14"


class AppState:
    is_draining: bool = False
    active_requests: int = 0
    active_requests_lock: threading.Lock = threading.Lock()
    shutdown_event: Optional[asyncio.Event] = None


app_state = AppState()


_SAFE_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_.:]{1,64}$")


@lru_cache(maxsize=4096)
def _get_vkid_from_hash(key_hash: str) -> str:
    return hmac.new(b"llm_shield_fingerprint_v2", key_hash.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def get_virtual_key_id(client_auth: str) -> str:
    """Computes a fast, cryptographically salted virtual key fingerprint using HMAC-SHA256.

    Uses SHA-256 pre-hash as LRU cache key to avoid storing raw API keys in plaintext memory.
    """
    if not client_auth:
        return "anonymous"
    key_hash = hashlib.sha256(client_auth.encode("utf-8")).hexdigest()
    return _get_vkid_from_hash(key_hash)


def _is_safe_ip(ip_str: str) -> bool:
    """Validates that resolved IP address is strictly public and safe from SSRF."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped
        return not (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
            or str(ip_obj) in ("255.255.255.255", "0.0.0.0")
        )
    except ValueError:
        return False


async def _resolve_and_validate_hostname(hostname: str) -> tuple[bool, Optional[str]]:
    """Asynchronously resolves A and AAAA DNS records in executor to avoid blocking ASGI loop."""
    if not hostname:
        return False, None
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except OSError:
        return False, None

    if not infos:
        return False, None

    resolved_ip = None
    for family, _, _, _, sockaddr in infos:
        ip_candidate = sockaddr[0]
        if not _is_safe_ip(ip_candidate):
            return False, None
        if resolved_ip is None:
            resolved_ip = ip_candidate

    return True, resolved_ip


async def _resolve_internal_hostname(hostname: str) -> Optional[str]:
    """Asynchronously resolves DNS records for trusted internal egress gateway."""
    if not hostname:
        return None
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except OSError:
        return None

    if not infos:
        return None

    # Return the first successfully resolved IP without SSRF restriction
    for family, _, _, _, sockaddr in infos:
        ip_candidate = sockaddr[0]
        return ip_candidate
    return None


class ConfigHandler(FileSystemEventHandler):
    """File watcher handler triggering dynamic configuration reloading."""

    def on_modified(self, event: Any) -> None:
        if event.src_path.endswith("config.yaml") or event.src_path.endswith(".env"):
            settings.reload()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application lifecycle, shared HTTP connection pools, and background observers."""
    import os

    app_state.is_draining = False
    app_state.shutdown_event = asyncio.Event()

    if settings.ENABLE_EXT_PROC:
        from llm_shield_proxy.api.grpc_service import serve_ext_proc
    from llm_shield_proxy.security.fips_kat import run_fips_kat_self_test
    from llm_shield_proxy.security.vault_client import vault_provider

    if not run_fips_kat_self_test():
        if settings.FIPS_STRICT_MODE:
            logging.getLogger("llm_shield.audit").critical(
                "FIPS 140-3 Cryptographic Integrity Self-Test Failed. Halting."
            )
            raise RuntimeError("FIPS 140-3 Cryptographic Integrity Self-Test Failed")
        else:
            logging.getLogger("llm_shield.audit").critical(
                "FIPS 140-3 KAT Failed, but FIPS_STRICT_MODE=False. Continuing."
            )

    AuditLogger.log_startup_event()

    from llm_shield_proxy.engines.pii_engine import pii_engine

    if pii_engine.enable_tier3:
        tier3_status = "Active (Production ONNX Model)" if settings.ONNX_MODEL_PATH else "Mock Session (Keyword fallback only - no ONNX model path configured)"
    else:
        tier3_status = "Disabled"

    print(
        f"--- LLM-Shield Proxy Startup Diagnostics ---\n"
        f"  PII Engine (Tier 1, 2): Active\n"
        f"  Tier 3 NER: {tier3_status}\n"
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

    if settings.WORKERS > 1:
        logger.warning(
            "SIGTERM graceful drain (active_requests counter) is process-local "
            "and does not coordinate across %d worker processes. "
            "For multi-worker drain, use an external load balancer health check.",
            settings.WORKERS,
        )

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

    shutdown_ev = app_state.shutdown_event

    async def _watch_policies() -> None:
        while not app_state.is_draining:
            try:
                settings.reload_policies()
            except Exception as e:
                logger.error(f"Policy reload task error: {e}")
            try:
                await asyncio.wait_for(shutdown_ev.wait(), timeout=settings.POLICIES_RELOAD_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    policy_watch_task = asyncio.create_task(_watch_policies())

    # Start gRPC ext_proc server in background
    grpc_server = None
    sock_path = settings.EXT_PROC_SOCK_PATH

    if settings.ENABLE_EXT_PROC:
        if os.name != "nt":
            sock_dir = os.path.dirname(sock_path)
            # SECURITY: Ensure the parent directory is restricted to proxy/envoy group
            # Apply the sticky bit (1) alongside 770 permissions
            os.makedirs(sock_dir, mode=0o1770, exist_ok=True)
            os.chmod(sock_dir, 0o1770)  # nosec B103

            if os.path.exists(sock_path):
                os.unlink(sock_path)

            # SECURITY: Prevent local privilege escalation (TOCTOU) by using umask
            # before socket creation, rather than chmod after creation.
            old_umask = os.umask(0o117)  # Inverts to 0o660
            try:
                grpc_server = await serve_ext_proc(sock_path)
            finally:
                os.umask(old_umask)
        else:
            grpc_server = await serve_ext_proc(sock_path)

    yield

    app_state.is_draining = True
    if shutdown_ev is not None and app_state.active_requests > 0:
        try:
            await asyncio.wait_for(shutdown_ev.wait(), timeout=settings.DRAIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error("Draining timeout reached. Forcefully shutting down.")

    policy_watch_task.cancel()
    try:
        await policy_watch_task
    except asyncio.CancelledError:
        pass

    observer.stop()
    await asyncio.get_running_loop().run_in_executor(None, observer.join)
    await app.state.http_client.aclose()

    from llm_shield_proxy.security.vault_client import vault_provider

    await vault_provider.aclose()

    # Cleanly close gRPC server
    if settings.ENABLE_EXT_PROC and grpc_server:
        grpc_server.close()
        await grpc_server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)

    from llm_shield_proxy.engines.crypto_vault import zeroize_crypto_material
    zeroize_crypto_material()
    logger.info("Zeroized cryptographic keys and AES-GCM material.")


app = FastAPI(
    title="LLM-Shield Proxy",
    description="Enterprise Zero-Egress Privacy Redaction Middleware Proxy",
    version=APP_VERSION,
    lifespan=lifespan,
)


app.include_router(health_router)
app.include_router(webhook_router)


@app.middleware("http")
async def security_and_tracing_middleware(request: Request, call_next: Any) -> Response:
    """Attaches correlation request IDs and enterprise HTTP security headers."""
    if app_state.is_draining:
        return JSONResponse(
            status_code=503, content={"error": {"message": "Service Unavailable: Pod Draining", "type": "server_error"}}
        )

    with app_state.active_requests_lock:
        app_state.active_requests += 1
    try:
        raw_id = request.headers.get("x-request-id", "")
        request_id = raw_id if (raw_id and _SAFE_REQUEST_ID_PATTERN.match(raw_id)) else str(uuid.uuid4())
        request.state.request_id = request_id

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    finally:
        # Lock wraps both the decrement and the drain-signal check to prevent a TOCTOU
        # race where active_requests transitions 1â†’0 between the read and set() calls.
        with app_state.active_requests_lock:
            app_state.active_requests -= 1
            if app_state.is_draining and app_state.active_requests == 0 and app_state.shutdown_event is not None:
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
    cumulative_size = 0

    stream_iter = request.stream()
    while True:
        try:
            async with asyncio.timeout(5.0):
                try:
                    chunk = await anext(stream_iter)
                except StopAsyncIteration:
                    break

            chunk_len = len(chunk)
            if cumulative_size + chunk_len > max_limit:
                raise ValueError("Payload Too Large")
            body_bytes.extend(chunk)
            cumulative_size += chunk_len
        except TimeoutError:
            from fastapi import HTTPException
            raise HTTPException(status_code=408, detail="Request Timeout: Slowloris prevention")

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


async def get_policy_resolver(request: Request) -> BasePolicyResolver:
    """Dependency injection provider for the active Pluggable RBAC Engine."""
    if not hasattr(request.app.state, "rbac_state"):
        request.app.state.rbac_state = {
            "cache": OrderedDict(),
            "cache_lock": asyncio.Lock(),
            "inflight_locks": BoundedLockMap(maxsize=1000),
            "background_tasks": set()
        }
    shared_state = request.app.state.rbac_state

    http_client = get_http_client(request)
    if settings.OPA_URL:
        return OPAPolicyResolver(http_client, settings.OPA_URL, shared_state)
    if settings.ENABLE_VAULT_SECRETS and settings.VAULT_ADDR and settings.VAULT_TOKEN:
        return VaultPolicyResolver(http_client, settings.VAULT_ADDR, settings.VAULT_TOKEN, shared_state)
    if hasattr(vault_store, "async_client"):
        return RedisPolicyResolver(vault_store.async_client, shared_state)
    return InMemoryPolicyResolver(shared_state)


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
    background_tasks: BackgroundTasks = None,
    policy_resolver: BasePolicyResolver = Depends(get_policy_resolver),
) -> Response:
    """Main reverse-proxy catch-all endpoint handling redaction and rehydration."""
    start_time = time.perf_counter()
    ctx = propagator.extract(request.headers)
    with tracer.start_as_current_span("proxy_catch_all", context=ctx):
        try:
            response = await _proxy_catch_all_internal(
                request,
                path,
                x_session_id,
                x_upstream_base_url,
                x_shield_masking_mode,
                background_tasks,
                policy_resolver,
            )
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
    background_tasks: Optional[BackgroundTasks] = None,
    policy_resolver: Optional[BasePolicyResolver] = None,
) -> Response:
    if path == "metrics":
        return await metrics_endpoint(request)

    # CORS Preflight
    if request.method == "OPTIONS":
        origin = request.headers.get("origin", "")
        allowed_origins = {o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()}
        if "*" in allowed_origins or (origin and origin in allowed_origins):
            allow_origin = origin or "*"
        elif not settings.CORS_ALLOWED_ORIGINS:
            allow_origin = origin or "*"
        else:
            allow_origin = "null"

        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": allow_origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-goog-api-key, x-session-id",
                "Access-Control-Max-Age": "86400",
            },
        )

    target_host = None

    if settings.AIR_GAPPED_MODE and settings.EGRESS_GATEWAY_URL:
        upstream_base = settings.EGRESS_GATEWAY_URL
        parsed = urlparse(upstream_base)
        if parsed.hostname:
            target_host = parsed.netloc
            resolved_ip = await _resolve_internal_hostname(parsed.hostname)
            if resolved_ip:
                ip_str = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
                port_str = f":{parsed.port}" if parsed.port else ""
                upstream_base = f"{parsed.scheme}://{ip_str}{port_str}{parsed.path}"
    else:
        upstream_base = settings.UPSTREAM_BASE_URL

    # SSRF Protection on Dynamic Client Upstream Override
    if x_upstream_base_url and settings.ALLOW_CLIENT_UPSTREAM_OVERRIDE:
        if x_upstream_base_url.startswith(("http://", "https://")):
            parsed = urlparse(x_upstream_base_url)
            hostname = parsed.hostname or ""
            target_host = parsed.netloc
            is_safe, resolved_ip = await _resolve_and_validate_hostname(hostname)
            if not is_safe or not resolved_ip:
                return JSONResponse(
                    status_code=403,
                    content={"error": {"message": "Forbidden upstream hostname", "type": "security_error"}},
                )
            # Overwrite upstream base with the resolved IP to prevent DNS rebinding SSRF
            ip_str = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            port_str = f":{parsed.port}" if parsed.port else ""
            upstream_base = f"{parsed.scheme}://{ip_str}{port_str}{parsed.path}"

    target_url = build_target_url(upstream_base, path)

    # Prepare forwarding headers
    headers = dict(request.headers)
    headers.pop("host", None)
    if target_host:
        headers["host"] = target_host
    headers.pop("content-length", None)
    headers.pop("accept-encoding", None)

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

    # Dynamic Virtual Key Resolution for FinOps & Tenant Scoping
    if virtual_key_id == "BYOK":
        if client_auth:
            virtual_key_id = get_virtual_key_id(client_auth)
        else:
            virtual_key_id = "anonymous"

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

    if settings.AIR_GAPPED_MODE and not settings.FORWARD_CLIENT_AUTH:
        headers.pop("authorization", None)
        headers.pop("x-api-key", None)
        headers.pop("x-goog-api-key", None)
        headers.pop("api-key", None)

    headers.pop("x-virtual-key-id", None)
    request_id = getattr(request.state, "request_id", None)
    request.state.virtual_key_id = virtual_key_id

    applied_role_name = "global_env"
    resolved_role = None

    if settings._flattened_policies:
        if virtual_key_id in settings._flattened_policies:
            resolved_role = settings._flattened_policies[virtual_key_id]
            applied_role_name = virtual_key_id
        elif "default_role" in settings._flattened_policies:
            resolved_role = settings._flattened_policies["default_role"]
            applied_role_name = "default_role"
        elif settings.SHIELD_FAILURE_MODE == "FAIL_CLOSED":
            return JSONResponse(
                status_code=403,
                content={"error": {"message": "Unauthorized virtual key", "type": "security_error"}},
            )

    if resolved_role:
        request_policy_ctx.set(resolved_role)
    else:
        request_policy_ctx.set({})

    # Agent Identity Enforcer (Edge-Level JWT/DPoP Validation)
    from llm_shield_proxy.security.identity import verify_agent_identity
    await verify_agent_identity(request, resolved_role)

    enable_canary_tripwire = settings.ENABLE_CANARY_TRIPWIRE
    enable_blast_radius_limits = settings.ENABLE_BLAST_RADIUS_LIMITS
    enable_finops_metering = settings.ENABLE_FINOPS_METERING

    from llm_shield_proxy.security.rate_limit import rate_limiter

    if not await rate_limiter.acquire(virtual_key_id):
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
            headers={"Retry-After": "1"},
        )

    from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault
    from llm_shield_proxy.engines.masking import MaskingMode, ScrubVault, resolve_masking_mode

    masking_mode = resolve_masking_mode(x_shield_masking_mode)

    if masking_mode == MaskingMode.STATELESS_CRYPTO:
        vault = StatelessCryptoVault()
    elif masking_mode == MaskingMode.SCRUB:
        vault = ScrubVault()  # type: ignore
    elif masking_mode == MaskingMode.HMAC:
        from llm_shield_proxy.engines.masking import HmacVault
        vault = HmacVault()  # type: ignore
    elif masking_mode == MaskingMode.STRUCTURAL_TAG:
        vault = await vault_store.get_vault_async(x_session_id, virtual_key_id)  # type: ignore
        vault.synthetic = False  # type: ignore
    else:  # SYNTHETIC
        vault = await vault_store.get_vault_async(x_session_id, virtual_key_id)  # type: ignore
        vault.synthetic = True  # type: ignore

    http_client = get_http_client(request)

    # Handle POST Payload Redaction
    if request.method == "POST":
        try:
            body_bytes = await read_body_with_limit(request)
            payload = orjson.loads(body_bytes) if body_bytes else {}
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

        if isinstance(payload, (dict, list)):
            if x_session_id and headers.get("x-shield-bypass-breaker", "").lower() != "true":
                try:
                    await check_circuit_breaker(x_session_id, payload)
                except CircuitBreakerTrippedException as cb_exc:
                    AuditLogger.log_circuit_breaker_tripped(
                        session_id=x_session_id,
                        request_id=request_id,
                        virtual_key_id=virtual_key_id,
                        consecutive_loops=cb_exc.consecutive_turns,
                        applied_role_name=applied_role_name,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "circuit_breaker_tripped",
                            "reason": "agent_loop_detected",
                            "consecutive_turns": cb_exc.consecutive_turns,
                        },
                        headers={"X-Shield-Circuit-Breaker": "TRIPPED", "Retry-After": "60"},
                    )

            # Pre-flight Watermark Check
            watermark_text = ""
            if settings.ENABLE_WATERMARKING and settings.SHIELD_WATERMARK_SECRET:
                response_format = payload.get("response_format", {})
                fmt_type = response_format.get("type", "") if isinstance(response_format, dict) else ""
                if "json" not in fmt_type.lower():
                    watermark_text = generate_watermark_text(
                        secret=settings.SHIELD_WATERMARK_SECRET,
                        virtual_key_id=virtual_key_id,
                        client_ip=request.client.host if request.client else None,
                        session_id=x_session_id or "unknown_session",
                    )

            is_streaming = bool(payload.get("stream", False))

            # Inject stream_options for FinOps usage extraction
            if is_streaming and enable_finops_metering:
                if "stream_options" not in payload:
                    payload["stream_options"] = {"include_usage": True}
                elif isinstance(payload["stream_options"], dict):
                    payload["stream_options"]["include_usage"] = True


            is_v3 = False
            v3_cipher = None
            try:
                is_json_rpc = isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"
                if is_json_rpc:
                    is_v3 = True
                    # Initialize v3 Stateless Engine
                    # Derive a 32-byte key from the watermark secret or fallback
                    raw_secret = settings.SHIELD_WATERMARK_SECRET or "default_shield_secret_for_v3_engine"
                    key = hashlib.sha256(raw_secret.encode('utf-8')).digest()

                    v3_cipher = StatelessPIICipher(key=key, version=1, session_id=x_session_id)
                    mutator = StatelessASTVisitor(v3_cipher)

                    try:
                        # Augment schemas dynamically
                        payload = DynamicSchemaRewriter.rewrite(payload)
                        # Process AST Mutator (orjson avoids GIL blocking)
                        redacted_bytes = await mutator.mutate(orjson.dumps(payload))
                        redacted_payload = orjson.loads(redacted_bytes)
                        entities_detected = 0 # Handled statelessly
                    except ASTDepthExceededException as ade:
                        # RFC 7807 compliant HTTP 400 error
                        return JSONResponse(
                            status_code=400,
                            content={
                                "type": "about:blank",
                                "title": "Bad Request",
                                "status": 400,
                                "detail": str(ade)
                            },
                            headers={"Content-Type": "application/problem+json"}
                        )
                else:
                    active_profile = pii_engine.get_profile(virtual_key_id)
                    if resolved_role and resolved_role.get("ENABLE_TIER3_ONNX_NER") is False:
                        from llm_shield_proxy.engines.pii_engine import CompiledProfile

                        active_profile = CompiledProfile(
                            name=active_profile.name,
                            tier1_patterns=active_profile.tier1_patterns,
                            tier3_ner_entities=set(),
                        )
                    old_entities_count = sum(vault.type_counters.values())

                    import contextvars
                    ctx = contextvars.copy_context()

                    redacted_payload = await asyncio.get_running_loop().run_in_executor(
                        None,
                        ctx.run,
                        pii_engine.redact_payload,
                        payload,
                        vault,
                        active_profile,  # type: ignore
                    )

                    new_entities_count = sum(vault.type_counters.values())
                    entities_detected = new_entities_count - old_entities_count

                if enable_blast_radius_limits and entities_detected > 0:
                    from llm_shield_proxy.security.rate_limit import blast_radius_limiter

                    if not await blast_radius_limiter.check_blast_radius(virtual_key_id, entities_detected):
                        AuditLogger.log_blast_radius_exceeded(
                            session_id=x_session_id,
                            virtual_key_id=virtual_key_id,
                            entities_count=entities_detected,
                            path=path,
                            request_id=request_id,
                            applied_role_name=applied_role_name,
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": {
                                    "message": "Data exfiltration threshold exceeded.",
                                    "type": "blast_radius_exceeded"
                                }
                            },
                            headers={"Retry-After": "60"},
                        )

                # Inject Canary Tripwire Token after PII redaction to prevent stripping invisible Unicode
                if enable_canary_tripwire and settings.CANARY_TOKEN:
                    directive = generate_watermark_text(
                        secret=settings.SHIELD_WATERMARK_SECRET or "default",
                        session_id=x_session_id or "unknown_session",
                        virtual_key_id=virtual_key_id,
                    )
                    if isinstance(redacted_payload, dict):
                        if "messages" in redacted_payload and isinstance(redacted_payload["messages"], list):
                            if redacted_payload["messages"] and redacted_payload["messages"][0].get("role") == "system" and isinstance(redacted_payload["messages"][0].get("content"), str):
                                redacted_payload["messages"][0]["content"] += directive
                            else:
                                redacted_payload["messages"].insert(0, {"role": "system", "content": directive})
                        elif "system" in redacted_payload:
                            if isinstance(redacted_payload["system"], str):
                                redacted_payload["system"] += directive
                            elif isinstance(redacted_payload["system"], list):
                                redacted_payload["system"].append({"type": "text", "text": directive})

                # target_provider is refined with payload
                target_provider = resolve_provider(dict(request.headers), redacted_payload)
                if target_provider == "anthropic":
                    anthropic_payload = AnthropicAdapter.transform_request(redacted_payload)
                    redacted_bytes = orjson.dumps(anthropic_payload)
                    target_url = "https://api.anthropic.com/v1/messages"
                    headers["anthropic-version"] = settings.ANTHROPIC_API_VERSION
                    headers["x-api-key"] = headers.get("authorization", "").replace("Bearer ", "").strip()
                    headers.pop("authorization", None)
                else:
                    redacted_bytes = orjson.dumps(redacted_payload)
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
                        content={
                            "error": {
                                "message": "DLP Inspection Failure: Request blocked by security policy",
                                "type": "dlp_failure",
                            }
                        },
                    )
                else:
                    logger.error(f"PII Engine failure (FAIL_OPEN): {e}")
                    redacted_bytes = body_bytes

            x_shield_fallback_url = request.headers.get("x-shield-fallback-url")
            max_retries = settings.MAX_RETRIES if settings.ENABLE_RETRY_FAILOVER else 0

            if is_streaming:
                attempt = 0
                current_target_url = target_url
                current_headers = dict(headers)
                upstream_res = None
                is_fallback = False

                while True:
                    req = http_client.build_request(
                        method=request.method,
                        url=current_target_url,
                        headers=current_headers,
                        content=redacted_bytes,
                    )
                    try:
                        upstream_res = await http_client.send(req, stream=True)
                        upstream_res.raise_for_status()
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError) as err:
                        if isinstance(err, httpx.HTTPStatusError):
                            upstream_res = err.response
                            # MUST explicitly close the leaked stream to free the HTTP/2 connection pool
                            await upstream_res.aclose()

                        status_code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else 503
                        if isinstance(err, httpx.HTTPStatusError) and status_code in (400, 401, 403):
                            break

                        if attempt < max_retries:
                            sleep_time = min(5.0, 0.5 * (2 ** attempt)) * random.uniform(0.5, 1.0)
                            AuditLogger.log_upstream_retry_attempt(x_session_id, request_id, virtual_key_id, attempt + 1, current_target_url, applied_role_name=applied_role_name)
                            await asyncio.sleep(sleep_time)
                            attempt += 1
                            continue

                        if settings.ENABLE_RETRY_FAILOVER and not is_fallback:
                            fallback_url = x_shield_fallback_url or settings.FALLBACK_BASE_URL
                            if fallback_url:
                                is_fallback = True
                                current_target_url = build_target_url(fallback_url, path)
                                if settings.FALLBACK_API_KEY:
                                    current_headers["authorization"] = f"Bearer {settings.FALLBACK_API_KEY}"
                                parsed_fallback = urlparse(fallback_url)
                                if parsed_fallback.hostname:
                                    current_headers["host"] = parsed_fallback.hostname
                                AuditLogger.log_provider_failover_triggered(x_session_id, request_id, virtual_key_id, fallback_url, applied_role_name=applied_role_name)
                                continue

                        break

                if upstream_res is None or upstream_res.is_error:
                    if upstream_res is not None:
                        try:
                            await upstream_res.aclose()
                        except Exception:
                            pass
                    status_code = upstream_res.status_code if (upstream_res and hasattr(upstream_res, 'status_code')) else 503
                    AuditLogger.log_redaction_event(
                        x_session_id,
                        vault.type_counters,
                        path,
                        virtual_key_id,
                        status_code,
                        request_id,
                        applied_role_name=applied_role_name,
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
                    applied_role_name=applied_role_name,
                )

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                async def wrapped_stream() -> AsyncGenerator[bytes, None]:
                    llm_shield_sse_active_streams.inc()
                    try:
                        if is_v3 and v3_cipher:
                            from llm_shield_proxy.engines.stateless_mutation_engine.streaming_lexer import (
                                StatelessStreamingLexer,
                            )
                            lexer = StatelessStreamingLexer(v3_cipher)
                            async for chunk in upstream_res.aiter_text():
                                safe_chunk = lexer.feed_chunk(chunk)
                                if safe_chunk:
                                    yield safe_chunk.encode("utf-8")
                            final_chunk = lexer.flush()
                            if final_chunk:
                                yield final_chunk.encode("utf-8")
                        else:
                            async for chunk in rehydrate_sse_stream(
                                upstream_res.aiter_bytes(),
                                vault,
                                watermark_text=watermark_text,  # type: ignore
                                path=path,
                                request_id=request_id,
                            ):
                                yield chunk
                    except (GeneratorExit, asyncio.CancelledError):
                        logger.info("Client disconnected mid-stream. Cleaning up SSE stream and upstream socket.")
                        if x_session_id:
                            if hasattr(vault_store, "clear_session_async"):
                                await vault_store.clear_session_async(x_session_id, virtual_key_id)
                            else:
                                vault_store.clear_session(x_session_id, virtual_key_id)
                        return
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
                attempt = 0
                current_target_url = target_url
                current_headers = dict(headers)
                upstream_res = None
                is_fallback = False

                while True:
                    try:
                        upstream_res = await http_client.request(
                            method=request.method,
                            url=current_target_url,
                            headers=current_headers,
                            content=redacted_bytes,
                        )
                        upstream_res.raise_for_status()
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError) as err:
                        logger.warning(f"Upstream request attempt failed: {err}")

                        if isinstance(err, httpx.HTTPStatusError):
                            upstream_res = err.response

                        status_code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else 503
                        if isinstance(err, httpx.HTTPStatusError) and status_code in (400, 401, 403):
                            break

                        if attempt < max_retries:
                            sleep_time = min(5.0, 0.5 * (2 ** attempt)) * random.uniform(0.5, 1.0)
                            AuditLogger.log_upstream_retry_attempt(x_session_id, request_id, virtual_key_id, attempt + 1, current_target_url, applied_role_name=applied_role_name)
                            await asyncio.sleep(sleep_time)
                            attempt += 1
                            continue

                        if settings.ENABLE_RETRY_FAILOVER and not is_fallback:
                            fallback_url = x_shield_fallback_url or settings.FALLBACK_BASE_URL
                            if fallback_url:
                                is_fallback = True
                                current_target_url = build_target_url(fallback_url, path)
                                if settings.FALLBACK_API_KEY:
                                    current_headers["authorization"] = f"Bearer {settings.FALLBACK_API_KEY}"
                                parsed_fallback = urlparse(fallback_url)
                                if parsed_fallback.hostname:
                                    current_headers["host"] = parsed_fallback.hostname
                                AuditLogger.log_provider_failover_triggered(x_session_id, request_id, virtual_key_id, fallback_url, applied_role_name=applied_role_name)
                                continue

                        break

                if upstream_res is None or upstream_res.is_error:
                    status_code = upstream_res.status_code if (upstream_res and hasattr(upstream_res, 'status_code')) else 503
                    AuditLogger.log_redaction_event(
                        x_session_id,
                        vault.type_counters,
                        path,
                        virtual_key_id,
                        status_code,
                        request_id,
                        applied_role_name=applied_role_name,
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
                    applied_role_name=applied_role_name,
                )

                res_headers = dict(upstream_res.headers)
                res_headers.pop("content-encoding", None)
                res_headers.pop("content-length", None)
                res_headers.pop("transfer-encoding", None)

                try:
                    if enable_canary_tripwire and settings.CANARY_TOKEN and settings.CANARY_TOKEN in upstream_res.text:
                        AuditLogger.log_tripwire_event(x_session_id, path, virtual_key_id, request_id, applied_role_name=applied_role_name)
                        logger.critical("Canary Tripwire triggered in standard REST response. Returning 403 Forbidden.")
                        return Response(content="Forbidden", status_code=403)

                    res_json = upstream_res.json()
                    loop = asyncio.get_running_loop()

                    if is_v3 and v3_cipher:
                        from llm_shield_proxy.engines.stateless_mutation_engine.streaming_lexer import (
                            NonStreamingRehydrator,
                        )
                        rehydrator = NonStreamingRehydrator(v3_cipher)
                        rehydrated_res = await loop.run_in_executor(None, rehydrator.rehydrate, res_json)
                    else:
                        if target_provider == "anthropic":
                            rehydrated_res = await loop.run_in_executor(None, _rehydrate_json_response, res_json, vault)
                            rehydrated_res = AnthropicAdapter.transform_response(rehydrated_res)
                        else:
                            rehydrated_res = await loop.run_in_executor(None, _rehydrate_json_response, res_json, vault)

                    if watermark_text:
                        if (
                            "choices" in rehydrated_res
                            and isinstance(rehydrated_res["choices"], list)
                            and rehydrated_res["choices"]
                        ):
                            msg = rehydrated_res["choices"][0].get("message", {})
                            if isinstance(msg, dict) and "content" in msg and isinstance(msg["content"], str):
                                msg["content"] += watermark_text
                        elif (
                            "content" in rehydrated_res
                            and isinstance(rehydrated_res["content"], list)
                            and rehydrated_res["content"]
                        ):
                            block = rehydrated_res["content"][0]
                            if isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
                                block["text"] += watermark_text

                    # FinOps Usage Metering for REST
                    if enable_finops_metering and background_tasks is not None:
                        usage = res_json.get("usage", {})
                        if usage and isinstance(usage, dict):
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)
                            total_tokens = usage.get("total_tokens", 0)
                            model = res_json.get("model", "unknown")

                            def _record_metrics(v_id: str, mdl: str, p_tok: int, c_tok: int, t_tok: int, s_id: Optional[str]) -> None:
                                try:
                                    from llm_shield_proxy.observability.metrics import llm_shield_tokens_total
                                    llm_shield_tokens_total.labels(virtual_key_id=v_id, model=mdl, type="prompt").inc(p_tok)
                                    llm_shield_tokens_total.labels(virtual_key_id=v_id, model=mdl, type="completion").inc(c_tok)
                                except Exception as e:
                                    logger.error(f"Failed to record token metrics: {e}")
                                AuditLogger.log_finops_metered(s_id, v_id, mdl, p_tok, c_tok, t_tok, applied_role_name=applied_role_name)

                            if total_tokens > 0:
                                background_tasks.add_task(_record_metrics, virtual_key_id, model, prompt_tokens, completion_tokens, total_tokens, x_session_id)

                    if getattr(settings, "ANONYMOUS_USAGE_TRACKING", True) and settings.TELEMETRY_ENDPOINT_URL:
                        _usage = res_json.get("usage", {}) if res_json else {}
                        _model = res_json.get("model", "unknown") if res_json else "unknown"
                        _total_tokens = _usage.get("total_tokens", 0) if isinstance(_usage, dict) else 0

                        _tel_payload = {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "project_id": "llm-shield-proxy",
                            "request_count": 1,
                            "token_count": _total_tokens,
                            "model": _model,
                        }
                        # Fire-and-forget; use shared http_client to avoid socket exhaustion
                        asyncio.create_task(dispatch_telemetry(settings.TELEMETRY_ENDPOINT_URL, _tel_payload, http_client))

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
    AuditLogger.log_proxy_event(x_session_id, path, request.method, virtual_key_id, 200, request_id, applied_role_name=applied_role_name)
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
