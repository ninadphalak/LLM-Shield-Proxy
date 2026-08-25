
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

    async def _detect_pii(self, value: str) -> bool:
        # Unified 3-Tier detection cascade (Regex + Shannon Entropy + NER / ONNX)
        spans = pii_engine.detect_spans(value)
        return len(spans) > 0

    async def mutate(self, payload: bytes) -> bytes:
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
                for k, v in node.items():
                    # Preserve JSON-RPC structural keys entirely
                    if k in ("jsonrpc", "method", "id"):
                        continue

                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}.{k}")
                        stack.append((v, f"{path}.{k}", depth + 1))
                    elif isinstance(v, str):
                        if await self._detect_pii(v):
                            aad = f"{path}.{k}"
                            cipher_text = self.cipher.encrypt(v, aad)
                            node[k] = {"_shield_val": "[REDACTED]", "_shield_ctx": cipher_text}

            elif isinstance(node, list):
                for i in range(len(node)):
                    v = node[i]
                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}[{i}]")
                        stack.append((v, f"{path}[{i}]", depth + 1))
                    elif isinstance(v, str):
                        if await self._detect_pii(v):
                            aad = f"{path}[{i}]"
                            cipher_text = self.cipher.encrypt(v, aad)
                            node[i] = {"_shield_val": "[REDACTED]", "_shield_ctx": cipher_text}

        return orjson.dumps(data)
