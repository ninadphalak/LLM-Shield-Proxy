import re
import hashlib
import json
from typing import Dict, Optional
from collections import OrderedDict
from faker import Faker
from llm_shield_proxy.config import settings

try:
    import redis
except ImportError:
    redis = None

fake = Faker()

class Vault:
    """
    Vault manages bidirectional deterministic mappings between original PII values
    and session-bound tokens (e.g., "sarah@example.com" <-> "[EMAIL_1]").
    """
    def __init__(self, save_callback=None):
        self.original_to_token: Dict[str, str] = {}
        self.token_to_original: Dict[str, str] = {}
        self.type_counters: Dict[str, int] = {}
        self.max_token_length: int = 0
        self.save_callback = save_callback

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        """
        Returns an existing token if original_val has already been registered,
        otherwise generates a deterministic token.
        """
        if original_val in self.original_to_token:
            return self.original_to_token[original_val]

        current_count = self.type_counters.get(entity_type, 0) + 1
        self.type_counters[entity_type] = current_count

        if settings.ENABLE_SYNTHETIC_SWAPPING:
            # Deterministic seed based on original value
            seed = int(hashlib.md5(original_val.encode()).hexdigest(), 16) % (2**32)
            Faker.seed(seed)
            if "PERSON" in entity_type:
                token = fake.first_name()
            elif "EMAIL" in entity_type:
                token = fake.email()
            elif "SSN" in entity_type:
                token = fake.ssn()
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
            self.save_callback(self)
            
        return token

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        """
        Replaces all tokens in the text with their corresponding original PII values.
        Respects a retention boundary: substitutions that intersect the last 
        `retention_length` characters are deferred to prevent Prefix-Free vulnerabilities.
        """
        if not text or not self.token_to_original:
            return text

        # Sort tokens by length descending to prevent partial token replacements
        sorted_tokens = sorted(self.token_to_original.keys(), key=len, reverse=True)
        result = text
        for token in sorted_tokens:
            original = self.token_to_original[token]
            if retention_length > 0:
                idx = 0
                while True:
                    idx = result.find(token, idx)
                    if idx == -1:
                        break
                    
                    if idx + len(token) > len(result) - retention_length:
                        # Touches boundary, defer this and subsequent matches
                        break
                        
                    result = result[:idx] + original + result[idx+len(token):]
                    idx += len(original)
            else:
                result = result.replace(token, original)

        return result


class VaultStore:
    """
    Store holding session-scoped vaults.
    Uses an OrderedDict to enforce a maximum capacity (LRU eviction) to prevent OOM memory leaks.
    """
    def __init__(self):
        self._sessions: OrderedDict[str, Vault] = OrderedDict()
        self.max_capacity = settings.MAX_SESSION_VAULTS

    def get_vault(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        if not session_id:
            return Vault()
        
        vault_key = f"{virtual_key_id}:{session_id}"
        
        if vault_key in self._sessions:
            self._sessions.move_to_end(vault_key)
            return self._sessions[vault_key]
        
        # Evict oldest if at capacity
        if len(self._sessions) >= self.max_capacity:
            self._sessions.popitem(last=False)
            
        new_vault = Vault()
        self._sessions[vault_key] = new_vault
        return new_vault

    def clear_session(self, session_id: str, virtual_key_id: str = "default"):
        vault_key = f"{virtual_key_id}:{session_id}"
        if vault_key in self._sessions:
            del self._sessions[vault_key]


class RedisVaultStore:
    """
    Store holding session-scoped vaults in Redis for horizontal multi-instance scaling.
    Uses rolling TTL expirations.
    """
    def __init__(self, redis_url: str):
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.ttl = settings.SESSION_TTL_SECONDS

    def get_vault(self, session_id: Optional[str] = None, virtual_key_id: str = "default") -> Vault:
        if not session_id:
            return Vault()
        
        vault_key = f"{virtual_key_id}:{session_id}"
        data = self.client.get(vault_key)
        
        def save_callback(vault: Vault):
            payload = {
                "original_to_token": vault.original_to_token,
                "token_to_original": vault.token_to_original,
                "type_counters": vault.type_counters,
                "max_token_length": vault.max_token_length
            }
            self.client.setex(vault_key, self.ttl, json.dumps(payload))
            
        vault = Vault(save_callback=save_callback)
        if data:
            try:
                parsed = json.loads(data)
                vault.original_to_token = parsed.get("original_to_token", {})
                vault.token_to_original = parsed.get("token_to_original", {})
                vault.type_counters = parsed.get("type_counters", {})
                vault.max_token_length = parsed.get("max_token_length", 0)
            except json.JSONDecodeError:
                pass
                
        # Rolling TTL on access
        self.client.expire(vault_key, self.ttl)
        return vault

    def clear_session(self, session_id: str, virtual_key_id: str = "default"):
        vault_key = f"{virtual_key_id}:{session_id}"
        self.client.delete(vault_key)


if settings.REDIS_URL:
    if not redis:
        raise ImportError("Redis is required when REDIS_URL is set. Run `pip install redis`.")
    vault_store = RedisVaultStore(settings.REDIS_URL)
else:
    vault_store = VaultStore()
