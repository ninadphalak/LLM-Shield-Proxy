
import asyncio
from typing import Any, Optional, Tuple

import orjson

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import (
    NO_NUMBER_SCAN_KEYS,
    _is_structural_key,
    _token_minter,
    normalize_and_desmuggle,
    pii_engine,
)

from .crypto import StatelessPIICipher


class ASTDepthExceededException(Exception):
    """Raised when the AST depth exceeds the circuit breaker limit."""
    pass

class StatelessASTVisitor:
    def __init__(self, cipher: StatelessPIICipher):
        self.cipher = cipher
        self.max_depth = settings.AST_MAX_DEPTH
        self.bracket_multiplier = settings.AST_BRACKET_MULTIPLIER

    def _count_pii(self, value: str) -> int:
        """Spans the redact_text cascade would find in `value`.

        Detection runs on the normalized text, exactly as redact_text does: on the raw
        leaf a zero-width space or a fullwidth `@` inside an identifier hid it from the
        regexes, and the value was forwarded in clear.
        """
        return len(pii_engine.detect_spans(normalize_and_desmuggle(value)))

    def _detect_pii(self, value: str) -> bool:
        # Unified 3-Tier detection cascade (Regex + Shannon Entropy + NER / ONNX), on
        # normalized text; see _count_pii.
        return self._count_pii(value) > 0

    @staticmethod
    def _number_text(value: Any) -> Optional[Tuple[str, str]]:
        """(decimal text, entity) for a validated identifier number, else None.

        Only checksum/structure-validated detectors apply to a number (Luhn-valid card,
        SSA-valid 9-digit SSN); the PHONE pattern matched every 7+ digit integer.
        """
        return pii_engine.detect_number(value)

    def _scan_leaf(self, value: Any, numbers: bool) -> Optional[Tuple[str, str, int]]:
        """(original text, faked text, span count) for a leaf carrying PII, else None."""
        if isinstance(value, str):
            hits = self._count_pii(value)
            return (value, self._fake(value), hits) if hits else None
        if not numbers:
            return None
        found = self._number_text(value)
        if found is None:
            return None
        text, entity_type = found
        return text, _token_minter(self._ephemeral_vault(), True)(text, entity_type), 1

    @staticmethod
    def _ephemeral_vault() -> Any:
        from llm_shield_proxy.engines.vault import Vault

        return Vault(synthetic=settings.ENABLE_SYNTHETIC_SWAPPING)

    @classmethod
    def _fake(cls, text: str) -> str:
        return pii_engine.redact_text(text, cls._ephemeral_vault())

    async def mutate(self, payload: bytes) -> bytes:
        """Sanitizes a JSON-RPC/MCP payload off the event loop.

        The traversal below is synchronous CPU work (JSON parse, stack-based
        walk, per-string PII detection). Running it directly on the event loop
        would stall every other concurrent request for the duration of a single
        large tool-call payload, so it's offloaded to a worker thread.
        """
        mutated, _ = await asyncio.to_thread(self._mutate_counted_sync, payload)
        return mutated

    async def mutate_counted(self, payload: bytes) -> Tuple[bytes, int]:
        """Like mutate(), also returning how many PII spans were masked.

        The gateway feeds the count to the entity-weighted blast-radius limiter, which
        must apply whichever redaction engine handled the body.
        """
        return await asyncio.to_thread(self._mutate_counted_sync, payload)

    def _mutate_sync(self, payload: bytes) -> bytes:
        return self._mutate_counted_sync(payload)[0]

    def _mutate_counted_sync(self, payload: bytes) -> Tuple[bytes, int]:
        # 1. Pre-FFI JSON Bomb Defense: Fast heuristic checking structural depth
        # If there are more total brackets than our absolute limit allows, reject early.
        if payload.count(b"{") + payload.count(b"[") > (self.max_depth * self.bracket_multiplier):
            raise ValueError("Security Exception: Payload structural complexity exceeds bounded limits.")

        data = orjson.loads(payload)
        entities = 0

        # Iterative stack tracking (node, path, depth, numbers). `numbers` is False below
        # a JSON Schema keyword or schema container: bounds and enums are not identifiers.
        stack = [(data, "$", 0, True)]

        while stack:
            node, path, depth, numbers = stack.pop()

            if depth >= self.max_depth:
                raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}")

            if isinstance(node, dict):
                context_fields = {}
                # Only the envelope is structural: the root object, or each request of a
                # batch. Deeper down, a key called `id` or `method` is a tool argument
                # like any other, and skipping it by name forwarded its value in clear.
                is_envelope = depth == 0 or (depth == 1 and isinstance(data, list))
                for k, v in list(node.items()):
                    if is_envelope and k in ("jsonrpc", "method", "id"):
                        continue

                    # A key is forwarded like a value: an identifier used as a property
                    # name is replaced one-way (there is nothing to restore it from).
                    # Only high-confidence detectors run on a key (see detect_key_spans).
                    if not _is_structural_key(k):
                        new_k, key_hits = pii_engine.redact_key_text(k, self._ephemeral_vault())
                        if key_hits:
                            entities += key_hits
                            while new_k in node or new_k in context_fields:
                                new_k = f"{new_k}_"
                            del node[k]
                            node[new_k] = v
                            k = new_k

                    child_numbers = numbers and k not in NO_NUMBER_SCAN_KEYS
                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}.{k}")
                        stack.append((v, f"{path}.{k}", depth + 1, child_numbers))
                        continue

                    scanned = self._scan_leaf(v, child_numbers)
                    if scanned:
                        text, faked_v, hits = scanned
                        entities += hits
                        ctx_key = f"_ctx_hash_{k}"
                        if ctx_key in node:
                            raise ValueError(f"Reserved stateless context field already present at {path}.{ctx_key}")
                        node[k] = faked_v
                        context_fields[ctx_key] = self.cipher.encrypt(text, k)

                node.update(context_fields)

            elif isinstance(node, list):
                for i in range(len(node)):
                    v = node[i]
                    if isinstance(v, (dict, list)):
                        if depth + 1 >= self.max_depth:
                            raise ASTDepthExceededException(f"Depth exceeded {self.max_depth} at {path}[{i}]")
                        stack.append((v, f"{path}[{i}]", depth + 1, numbers))
                        continue
                    scanned = self._scan_leaf(v, numbers)
                    if scanned:
                        text, faked_v, hits = scanned
                        entities += hits
                        cipher_text = self.cipher.encrypt(text, faked_v)
                        node[i] = {"_shield_val": faked_v, "_shield_ctx": cipher_text}

        return orjson.dumps(data), entities
