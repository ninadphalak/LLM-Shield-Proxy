import re
from typing import Dict, Optional


class Vault:
    """
    Vault manages bidirectional deterministic mappings between original PII values
    and session-bound tokens (e.g., "sarah@example.com" <-> "[EMAIL_1]").
    """
    def __init__(self):
        self.original_to_token: Dict[str, str] = {}
        self.token_to_original: Dict[str, str] = {}
        self.type_counters: Dict[str, int] = {}

    def get_or_create_token(self, original_val: str, entity_type: str) -> str:
        """
        Returns an existing token if original_val has already been registered,
        otherwise generates a deterministic token like [PERSON_1].
        """
        if original_val in self.original_to_token:
            return self.original_to_token[original_val]

        current_count = self.type_counters.get(entity_type, 0) + 1
        self.type_counters[entity_type] = current_count

        token = f"[{entity_type}_{current_count}]"
        self.original_to_token[original_val] = token
        self.token_to_original[token] = original_val
        return token

    def rehydrate(self, text: str) -> str:
        """
        Replaces all tokens in the text with their corresponding original PII values.
        """
        if not text or not self.token_to_original:
            return text

        # Sort tokens by length descending to prevent partial token replacements
        sorted_tokens = sorted(self.token_to_original.keys(), key=len, reverse=True)
        result = text
        for token in sorted_tokens:
            original = self.token_to_original[token]
            result = result.replace(token, original)

        return result


class VaultStore:
    """
    Store holding session-scoped vaults.
    If session_id is provided, vault persists across requests for that session_id.
    Otherwise, an ephemeral Vault is returned for a single request.
    """
    def __init__(self):
        self._sessions: Dict[str, Vault] = {}

    def get_vault(self, session_id: Optional[str] = None) -> Vault:
        if not session_id:
            return Vault()
        
        if session_id not in self._sessions:
            self._sessions[session_id] = Vault()
        return self._sessions[session_id]

    def clear_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]


vault_store = VaultStore()
