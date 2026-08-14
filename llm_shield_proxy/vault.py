"""Enterprise Session-Scoped Vault Management Module.

Provides bidirectional deterministic mapping between sensitive original PII values
and session-bound tokens (or realistic synthetic values), enforcing isolation across tenants.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional
from faker import Faker
from llm_shield_proxy.config import settings

try:
    import redis.asyncio as aioredis
    import redis
except ImportError:
    aioredis = None  # type: ignore
    redis = None  # type: ignore

fake = Faker()


class Vault:
    """Session-scoped bidirectional PII token mapping vault.

    Maintains deterministic bijective mappings between raw PII text values
    and temporary session tokens. Supports both bracketed structural tagging
    ([PERSON_1], [EMAIL_1]) and unbracketed synthetic entity swapping.

    Thread-safe and supports multi-tenant namespace isolation.
    """

    def __init__(self, tenant_id: str = "default", session_id: str = "default", synthetic: Optional[bool] = None) -> None:
        self.tenant_id: str = tenant_id
        self.session_id: str = session_id
        self.synthetic: bool = synthetic if synthetic is not None else settings.ENABLE_SYNTHETIC_SWAPPING
        self.original_to_token: Dict[str, str] = {}
        self.token_to_original: Dict[str, str] = {}
        self.type_counters: Dict[str, int] = {}
        self.max_token_length: int = 0
        self._lock: threading.Lock = threading.Lock()
        self.save_callback: Optional[Callable[[Vault], None]] = None

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        """Retrieves an existing token or generates a deterministic replacement.

        Args:
            original_val: The raw sensitive string (e.g. 'Jane Doe', '555-0199').
            entity_type: The detected entity classifier (e.g. 'PERSON', 'SSN').

        Returns:
            A deterministic session-unique token or synthetic word string.
        """
        with self._lock:
            if original_val in self.original_to_token:
                return self.original_to_token[original_val]

            current_count = self.type_counters.get(entity_type, 0) + 1
            self.type_counters[entity_type] = current_count

            if self.synthetic:
                seed = int(hashlib.md5(original_val.encode("utf-8")).hexdigest(), 16) % (2**32)
                Faker.seed(seed)
                if "PERSON" in entity_type or "NAME" in entity_type:
                    token = fake.first_name()
                elif "EMAIL" in entity_type:
                    token = fake.email()
                elif "SSN" in entity_type:
                    token = fake.ssn()
                elif "PHONE" in entity_type:
                    token = fake.phone_number()
                elif "IP" in entity_type:
                    token = fake.ipv4()
                elif "CREDIT_CARD" in entity_type:
                    token = fake.credit_card_number()
                elif "KEY" in entity_type or "SECRET" in entity_type or "TOKEN" in entity_type or "PAT" in entity_type:
                    token = f"AKIA{''.join(random.Random(seed).choices('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=16))}"
                elif "GPE" in entity_type or "LOC" in entity_type:
                    token = fake.city()
                else:
                    token = fake.word()
            else:
                token = f"[{entity_type}_{current_count}]"

            self.original_to_token[original_val] = token
            self.token_to_original[token] = original_val
            self.max_token_length = max(self.max_token_length, len(token))

            if self.save_callback:
                try:
                    self.save_callback(self)
                except Exception:
                    pass

            return token

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        """Replaces all registered tokens in the text with original raw PII.

        Respects retention boundary: any token occurrences whose replacement would
        cross or touch the trailing `retention_length` characters are deferred
        to prevent partial-token or prefix-free leakage across stream chunks.

        Time Complexity: O(N * M) where N is text length and M is total token count.
        Space Complexity: O(N) string allocation.

        Args:
            text: Input string containing redacted tokens or synthetic words.
            retention_length: Number of trailing characters at buffer boundary to protect.

        Returns:
            Rehydrated text with tokens restored to original values.
        """
        if not text or not self.token_to_original:
            return text

        # Sort tokens by length descending to prevent partial token prefix collisions
        sorted_tokens = sorted(self.token_to_original.keys(), key=len, reverse=True)
        result = text

        for token in sorted_tokens:
            original = self.token_to_original[token]
            if retention_length > 0:
                pos = 0
                while pos < len(result):
                    idx = result.find(token, pos)
                    if idx == -1:
                        break

                    # Check if token crosses into the trailing retention window
                    if idx + len(token) > len(result) - retention_length:
                        # Defer replacement of this and subsequent overlapping matches
                        break

                    result = result[:idx] + original + result[idx + len(token):]
                    pos = idx + len(original)
            else:
                result = result.replace(token, original)

        return result


class VaultStore:
    """Thread-safe in-memory session vault store with LRU eviction.

    Attributes:
        max_capacity: Maximum number of active tenant session vaults.
    """

    def __init__(self, max_capacity: Optional[int] = None) -> None:
        self._sessions: OrderedDict[str, Vault] = OrderedDict()
        self.max_capacity: int = max_capacity or settings.MAX_SESSION_VAULTS
        self._lock: threading.Lock = threading.Lock()

    def get_vault(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        """Retrieves or creates an in-memory session vault.

        Args:
            session_id: Unique client session identifier.
            virtual_key_id: Tenant or virtual key identifier for namespace isolation.

        Returns:
            The session-bound Vault instance.
        """
        if not session_id:
            return Vault()

        vault_key = f"{virtual_key_id}:{session_id}"
        with self._lock:
            if vault_key in self._sessions:
                self._sessions.move_to_end(vault_key)
                return self._sessions[vault_key]

            # Evict oldest LRU vault if at capacity
            if len(self._sessions) >= self.max_capacity:
                self._sessions.popitem(last=False)

            new_vault = Vault()
            self._sessions[vault_key] = new_vault
            return new_vault

    async def get_vault_async(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        """Async-compatible interface for retrieving in-memory vault."""
        return self.get_vault(session_id, virtual_key_id)

    def clear_session(self, session_id: str, virtual_key_id: str = "default") -> None:
        """Removes a session vault from the in-memory cache."""
        vault_key = f"{virtual_key_id}:{session_id}"
        with self._lock:
            self._sessions.pop(vault_key, None)


class RedisVaultStore:
    """Distributed Redis-backed session vault store with rolling TTLs.

    Supports horizontal multi-instance scaling using async and sync Redis pools.
    """

    def __init__(self, redis_url: str) -> None:
        self.redis_url: str = redis_url
        self.ttl: int = settings.SESSION_TTL_SECONDS
        self._sync_client: Optional[redis.Redis] = None
        self._async_client: Optional[aioredis.Redis] = None

    @property
    def sync_client(self) -> redis.Redis:
        """Lazy-loaded synchronous Redis client."""
        if self._sync_client is None:
            if not redis:
                raise ImportError("redis package required for RedisVaultStore. Run `pip install redis`.")
            self._sync_client = redis.from_url(self.redis_url, decode_responses=True)
        return self._sync_client

    @property
    def async_client(self) -> aioredis.Redis:
        """Lazy-loaded asynchronous Redis client with connection pooling."""
        if self._async_client is None:
            if not aioredis:
                raise ImportError("redis.asyncio required for async Redis vault. Run `pip install redis`.")
            self._async_client = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._async_client

    def get_vault(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        """Synchronous vault retrieval for backward compatibility and sync tests."""
        if not session_id:
            return Vault()

        vault_key = f"{virtual_key_id}:{session_id}"
        data = self.sync_client.get(vault_key)

        def save_callback(v: Vault) -> None:
            payload = {
                "original_to_token": v.original_to_token,
                "token_to_original": v.token_to_original,
                "type_counters": v.type_counters,
                "max_token_length": v.max_token_length,
            }
            self.sync_client.setex(vault_key, self.ttl, json.dumps(payload))

        vault = Vault(save_callback=save_callback)
        if data:
            try:
                parsed = json.loads(data)
                vault.original_to_token = parsed.get("original_to_token", {})
                vault.token_to_original = parsed.get("token_to_original", {})
                vault.type_counters = parsed.get("type_counters", {})
                vault.max_token_length = parsed.get("max_token_length", 0)
            except (json.JSONDecodeError, TypeError):
                pass

        self.sync_client.expire(vault_key, self.ttl)
        return vault

    async def get_vault_async(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        """Non-blocking async Redis vault retrieval using redis.asyncio."""
        if not session_id:
            return Vault()

        vault_key = f"{virtual_key_id}:{session_id}"
        data = await self.async_client.get(vault_key)

        def save_callback(v: Vault) -> None:
            payload = {
                "original_to_token": v.original_to_token,
                "token_to_original": v.token_to_original,
                "type_counters": v.type_counters,
                "max_token_length": v.max_token_length,
            }
            self.sync_client.setex(vault_key, self.ttl, json.dumps(payload))

        vault = Vault(save_callback=save_callback)
        if data:
            try:
                parsed = json.loads(data)
                vault.original_to_token = parsed.get("original_to_token", {})
                vault.token_to_original = parsed.get("token_to_original", {})
                vault.type_counters = parsed.get("type_counters", {})
                vault.max_token_length = parsed.get("max_token_length", 0)
            except (json.JSONDecodeError, TypeError):
                pass

        await self.async_client.expire(vault_key, self.ttl)
        return vault

    def clear_session(self, session_id: str, virtual_key_id: str = "default") -> None:
        """Deletes session key from Redis."""
        vault_key = f"{virtual_key_id}:{session_id}"
        self.sync_client.delete(vault_key)

    async def clear_session_async(self, session_id: str, virtual_key_id: str = "default") -> None:
        """Asynchronously deletes session key from Redis."""
        vault_key = f"{virtual_key_id}:{session_id}"
        await self.async_client.delete(vault_key)

    async def ping_async(self) -> bool:
        """Checks connectivity to the Redis instance."""
        try:
            return bool(await self.async_client.ping())
        except Exception:
            return False


def create_vault_store() -> VaultStore | RedisVaultStore:
    """Factory creating configured vault store."""
    if settings.REDIS_URL:
        if not redis:
            raise ImportError("Redis is required when REDIS_URL is set. Run `pip install redis`.")
        return RedisVaultStore(settings.REDIS_URL)
    return VaultStore()


vault_store = create_vault_store()
