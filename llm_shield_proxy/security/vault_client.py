"""Lightweight HashiCorp Vault Integration.

Provides an `AsyncVaultSecretProvider` to securely fetch and cache
dynamic secrets from Vault using httpx, preventing I/O blocking
in the hot path of the proxy.
"""

import asyncio
import logging
import os
from typing import Dict, Optional

import httpx

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)


class AsyncVaultSecretProvider:
    """Lightweight async Vault secret provider caching secrets in-memory."""

    def __init__(self):
        self._cached_secrets: Dict[str, str] = {}
        self._refresh_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self):
        """Close HTTP client and cancel background refresh task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _authenticate(self) -> str:
        """Authenticate with Vault and return a client token."""
        if not settings.VAULT_ADDR:
            raise ValueError("VAULT_ADDR is not configured")

        method = settings.VAULT_AUTH_METHOD

        if method == "TOKEN":
            if not settings.VAULT_TOKEN:
                raise ValueError("VAULT_TOKEN is required for TOKEN auth method")
            return settings.VAULT_TOKEN

        elif method == "APPROLE":
            if not settings.VAULT_ROLE_ID or not settings.VAULT_SECRET_ID:
                raise ValueError("VAULT_ROLE_ID and VAULT_SECRET_ID are required for APPROLE auth method")

            url = f"{settings.VAULT_ADDR.rstrip('/')}/v1/auth/approle/login"
            payload = {"role_id": settings.VAULT_ROLE_ID, "secret_id": settings.VAULT_SECRET_ID}
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["auth"]["client_token"]

        elif method == "KUBERNETES":
            role = settings.VAULT_K8S_ROLE
            if not role:
                raise ValueError("VAULT_K8S_ROLE is required for KUBERNETES auth method")

            jwt_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
            if not os.path.exists(jwt_path):
                raise ValueError(f"Kubernetes service account token not found at {jwt_path}")

            with open(jwt_path, "r") as f:
                jwt = f.read().strip()

            url = f"{settings.VAULT_ADDR.rstrip('/')}/v1/auth/kubernetes/login"
            payload = {"role": role, "jwt": jwt}
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["auth"]["client_token"]

        else:
            raise ValueError(f"Unsupported Vault auth method: {method}")

    async def fetch_secrets(self) -> None:
        """Fetch secrets from Vault KV store and update the cache atomically."""
        if not settings.VAULT_ADDR or not settings.VAULT_SECRET_PATH:
            return

        try:
            token = await self._authenticate()
            headers = {"X-Vault-Token": token}

            # Format URL for KV v2 or v1
            path = settings.VAULT_SECRET_PATH.strip("/")
            # KV v2 usually has 'data' in the path, e.g., secret/data/llm-shield/keys
            # If the user provides the full path, we just query it.
            url = f"{settings.VAULT_ADDR.rstrip('/')}/v1/{path}"

            resp = await self.client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # Extract secrets, supporting both KV v1 and v2 formats
            # KV v2 wraps secrets in 'data.data', KV v1 is just 'data'
            if "data" in data and "data" in data["data"]:
                secrets = data["data"]["data"]
            elif "data" in data:
                secrets = data["data"]
            else:
                secrets = {}

            # Atomic swap to avoid race conditions
            self._cached_secrets = {str(k): str(v) for k, v in secrets.items()}
            logger.info(f"Successfully refreshed {len(self._cached_secrets)} secrets from Vault.")

        except httpx.HTTPError as e:
            # We don't want to dump the actual response body if it contains secrets,
            # though an HTTP error might not. Just log the status and path safely.
            status_code = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
            logger.error(f"Vault HTTPError during fetch_secrets: status={status_code}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during Vault fetch_secrets: {e}")
            raise

    async def _refresh_loop(self) -> None:
        """Background loop to refresh secrets periodically."""
        interval = settings.VAULT_REFRESH_INTERVAL_SECONDS
        while True:
            await asyncio.sleep(interval)
            try:
                await self.fetch_secrets()
            except asyncio.CancelledError:
                logger.info("Vault refresh loop gracefully cancelled.")
                break
            except Exception as e:
                # Log but do not overwrite cached secrets
                logger.error(f"Background Vault refresh failed: {e}. Retaining previously cached secrets.")
                try:
                    from llm_shield_proxy.observability.metrics import llm_shield_vault_refresh_errors_total

                    llm_shield_vault_refresh_errors_total.inc()
                except ImportError:
                    pass

    def start_background_refresh(self) -> None:
        """Start the background refresh loop if not already running."""
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    def get_secret(self, key: str) -> Optional[str]:
        """O(1) memory lookup for a cached secret."""
        return self._cached_secrets.get(key)


vault_provider = AsyncVaultSecretProvider()
