import asyncio
import logging
import time
from typing import Optional

from cachetools import TTLCache

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import RedisVaultStore, vault_store

# Token bucket Lua script for Redis:
# KEYS[1]: the rate limit key
# ARGV[1]: rate (tokens per millisecond)
# ARGV[2]: burst (maximum capacity)
# ARGV[3]: now (current timestamp in milliseconds)
# ARGV[4]: 1 (tokens requested)
RATE_LIMIT_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local info = redis.call('HMGET', key, 'tokens', 'last_refresh')
local tokens = tonumber(info[1])
local last_refresh = tonumber(info[2])

if not tokens then
    tokens = burst
    last_refresh = now
end

local time_passed = math.max(0, now - last_refresh)
local new_tokens = time_passed * rate
tokens = math.min(burst, tokens + new_tokens)

if tokens >= requested then
    tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refresh', now)
    redis.call('PEXPIRE', key, math.ceil(burst / rate))
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refresh', now)
    redis.call('PEXPIRE', key, math.ceil(burst / rate))
    return 0
end
"""


class InMemoryBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refresh = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> bool:
        now = time.monotonic()
        async with self.lock:
            time_passed = max(0.0, now - self.last_refresh)
            self.tokens = min(float(self.burst), self.tokens + time_passed * self.rate)
            self.last_refresh = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class DistributedRateLimiter:
    def __init__(self):
        maxsize = getattr(settings, "RATE_LIMIT_LOCAL_CACHE_MAXSIZE", 50_000)
        ttl = getattr(settings, "RATE_LIMIT_LOCAL_CACHE_TTL_SECONDS", 3600)
        self._in_memory_buckets: TTLCache[str, InMemoryBucket] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lua_sha: Optional[str] = None
        self._lock = asyncio.Lock()

    async def acquire(self, virtual_key_id: str) -> bool:
        if not settings.ENABLE_RATE_LIMITING:
            return True

        rate_per_sec = settings.RATE_LIMIT_RPM / 60.0
        burst = settings.RATE_LIMIT_BURST

        vs = vault_store
        if isinstance(vs, RedisVaultStore) and getattr(vs, "async_client", None) is not None:
            try:
                now_ms = int(time.time() * 1000)
                rate_per_ms = rate_per_sec / 1000.0
                key = f"rate_limit:{virtual_key_id}"

                if not self._lua_sha:
                    async with self._lock:
                        if not self._lua_sha:
                            self._lua_sha = await vs.async_client.script_load(RATE_LIMIT_LUA)  # type: ignore

                result = await vs.async_client.evalsha(self._lua_sha, 1, key, rate_per_ms, burst, now_ms, 1)  # type: ignore
                return bool(result)
            except Exception:  # nosec B110 noqa: S110
                # Security Note: Fallback to in-memory on Redis failure to ensure fail-open
                # Fallback to in-memory on Redis failure
                pass

        # Fallback to in-memory bucket
        if virtual_key_id not in self._in_memory_buckets:
            async with self._lock:
                if virtual_key_id not in self._in_memory_buckets:
                    self._in_memory_buckets[virtual_key_id] = InMemoryBucket(rate_per_sec, burst)

        return await self._in_memory_buckets[virtual_key_id].acquire()


rate_limiter = DistributedRateLimiter()

class DistributedBlastRadiusLimiter:
    """Entity-Weighted Blast Radius Circuit Breaker (Phase 2).

    Evaluates the volume of sensitive PII entities swapped per minute.
    Acts as a fail-safe that halts compromised agents attempting bulk data exfiltration.
    """
    def __init__(self):
        maxsize = getattr(settings, "RATE_LIMIT_LOCAL_CACHE_MAXSIZE", 50_000)
        ttl = getattr(settings, "RATE_LIMIT_LOCAL_CACHE_TTL_SECONDS", 3600)
        self._in_memory_buckets: TTLCache[str, InMemoryBucket] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lua_sha: Optional[str] = None
        self._lock = asyncio.Lock()

    async def check_blast_radius(self, virtual_key_id: str, requested: int) -> bool:
        if not settings.ENABLE_BLAST_RADIUS_LIMITS:
            return True

        if requested <= 0:
            return True

        rate_per_sec = settings.BLAST_RADIUS_REPLENISH_RATE_PER_MIN / 60.0
        burst = settings.BLAST_RADIUS_BURST_CAPACITY

        vs = vault_store
        if isinstance(vs, RedisVaultStore) and getattr(vs, "async_client", None) is not None:
            try:
                now_ms = int(time.time() * 1000)
                rate_per_ms = rate_per_sec / 1000.0
                key = f"blast_radius:{virtual_key_id}"

                if not self._lua_sha:
                    async with self._lock:
                        if not self._lua_sha:
                            self._lua_sha = await vs.async_client.script_load(RATE_LIMIT_LUA)  # type: ignore

                result = await vs.async_client.evalsha(self._lua_sha, 1, key, rate_per_ms, burst, now_ms, requested)  # type: ignore
                return bool(result)
            except Exception as e:
                # Catch redis.exceptions.RedisError and any other connection errors
                logging.getLogger(__name__).warning("Redis error during blast radius evaluation, failing open: %s", e)
                return True

        # Fallback to in-memory bucket if no Redis (but we still want to limit)
        if virtual_key_id not in self._in_memory_buckets:
            async with self._lock:
                if virtual_key_id not in self._in_memory_buckets:
                    self._in_memory_buckets[virtual_key_id] = InMemoryBucket(rate_per_sec, burst)

        # InMemoryBucket currently only supports acquire(1), we need to acquire(requested)
        # We will dynamically adapt the InMemoryBucket for multiple tokens if needed,
        # but for simplicity in fallback, we'll implement a quick inline check for requested.
        bucket = self._in_memory_buckets[virtual_key_id]
        now = time.monotonic()
        async with bucket.lock:
            time_passed = max(0.0, now - bucket.last_refresh)
            bucket.tokens = min(float(bucket.burst), bucket.tokens + time_passed * bucket.rate)
            bucket.last_refresh = now
            if bucket.tokens >= requested:
                bucket.tokens -= requested
                return True
            return False

blast_radius_limiter = DistributedBlastRadiusLimiter()
