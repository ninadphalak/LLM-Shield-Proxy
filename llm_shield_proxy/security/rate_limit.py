import asyncio
import time
from typing import Optional

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
        self._in_memory_buckets: dict[str, InMemoryBucket] = {}
        self._lua_sha: Optional[str] = None
        self._lock = asyncio.Lock()

    async def acquire(self, virtual_key_id: str) -> bool:
        if not settings.ENABLE_RATE_LIMITING:
            return True

        rate_per_sec = settings.RATE_LIMIT_RPM / 60.0
        burst = settings.RATE_LIMIT_BURST

        if isinstance(vault_store, RedisVaultStore) and vault_store.redis is not None:
            try:
                now_ms = int(time.time() * 1000)
                rate_per_ms = rate_per_sec / 1000.0
                key = f"rate_limit:{virtual_key_id}"

                if not self._lua_sha:
                    async with self._lock:
                        if not self._lua_sha:
                            self._lua_sha = await vault_store.redis.script_load(RATE_LIMIT_LUA)

                result = await vault_store.redis.evalsha(
                    self._lua_sha, 1, key, rate_per_ms, burst, now_ms, 1
                )
                return bool(result)
            except Exception:
                # Fallback to in-memory on Redis failure
                pass

        # Fallback to in-memory bucket
        if virtual_key_id not in self._in_memory_buckets:
            async with self._lock:
                if virtual_key_id not in self._in_memory_buckets:
                    self._in_memory_buckets[virtual_key_id] = InMemoryBucket(rate_per_sec, burst)

        return await self._in_memory_buckets[virtual_key_id].acquire()

rate_limiter = DistributedRateLimiter()
