"""LLM-Shield-Proxy: Enterprise Zero-Egress Privacy Redaction Proxy."""

from llm_shield_proxy.config import settings
from llm_shield_proxy.vault import Vault, VaultStore, vault_store
from llm_shield_proxy.pii_engine import PIIEngine, pii_engine
from llm_shield_proxy.streaming import SSERehydrationBuffer, rehydrate_sse_stream
from llm_shield_proxy.audit import AuditLogger

__version__ = "1.0.17"

__all__ = [
    "settings",
    "Vault",
    "VaultStore",
    "vault_store",
    "PIIEngine",
    "pii_engine",
    "SSERehydrationBuffer",
    "rehydrate_sse_stream",
    "AuditLogger",
    "__version__",
]
