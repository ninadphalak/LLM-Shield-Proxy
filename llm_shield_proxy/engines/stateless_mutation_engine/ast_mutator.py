
import asyncio

import orjson

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine

from .crypto import StatelessPIICipher


class ASTDepthExceededException(Exception):
    """Raised when the AST depth exceeds the circuit breaker limit."""
    pass

class StatelessASTVisitor:
    def __init__(self, cipher: StatelessPIICipher):
        self.cipher = cipher
        self.max_depth = settings.AST_MAX_DEPTH
        self.bracket_multiplier = settings.AST_BRACKET_MULTIPLIER

    def _detect_pii(self, value: str) -> bool:
        # Unified 3-Tier detection cascade (Regex + Shannon Entropy + NER / ONNX)
        spans = pii_engine.detect_spans(value)
        return len(spans) > 0

    async def mutate(self, payload: bytes) -> bytes:
        """Sanitizes a JSON-RPC/MCP payload off the event loop.

        The traversal below is synchronous CPU work (JSON parse, stack-based
        walk, per-string PII detection). Running it directly on the event loop
        would stall every other concurrent request for the duration of a single
        large tool-call payload, so it's offloaded to a worker thread.
        """
        return await asyncio.to_thread(self._mutate_sync, payload)

    def _mutate_sync(self, payload: bytes) -> bytes:
        # 1. Pre-FFI JSON Bomb Defense: Fast heuristic checking structural depth
        # If there are more total brackets than our absolute limit allows, reject early.
        if payload.count(b"{") + payload.count(b"[") > (self.max_depth * self.bracket_multiplier):
            raise ValueError("Security Exception: Payload structural complexity exceeds bounded limits.")

        data = orjson.loads(payload)

        # Iterative stack tracking (node, path, depth)
        stack = [(data, "$", 0)]

        while stack:
            node, path, depth = stack.pop()

            if depth >= self.max_depth:
                raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}")

            if isinstance(node, dict):
                context_fields = {}
                for k, v in list(node.items()):
                    # Preserve JSON-RPC structural keys entirely
                    if k in ("jsonrpc", "method", "id"):
                        continue

                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}.{k}")
                        stack.append((v, f"{path}.{k}", depth + 1))
                    elif isinstance(v, str):
                        if self._detect_pii(v):
                            from llm_shield_proxy.engines.vault import Vault
                            ephemeral_vault = Vault(synthetic=settings.ENABLE_SYNTHETIC_SWAPPING)
                            faked_v = pii_engine.redact_text(v, ephemeral_vault)
                            ctx_key = f"_ctx_hash_{k}"
                            if ctx_key in node:
                                raise ValueError(f"Reserved stateless context field already present at {path}.{ctx_key}")
                            node[k] = faked_v
                            context_fields[ctx_key] = self.cipher.encrypt(v, k)

                node.update(context_fields)

            elif isinstance(node, list):
                for i in range(len(node)):
                    v = node[i]
                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}[{i}]")
                        stack.append((v, f"{path}[{i}]", depth + 1))
                    elif isinstance(v, str):
                        if self._detect_pii(v):
                            from llm_shield_proxy.engines.vault import Vault
                            ephemeral_vault = Vault(synthetic=settings.ENABLE_SYNTHETIC_SWAPPING)
                            faked_v = pii_engine.redact_text(v, ephemeral_vault)
                            cipher_text = self.cipher.encrypt(v, faked_v)
                            node[i] = {"_shield_val": faked_v, "_shield_ctx": cipher_text}

        return orjson.dumps(data)
