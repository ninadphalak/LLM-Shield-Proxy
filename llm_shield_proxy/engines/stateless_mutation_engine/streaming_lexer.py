import io
import json
import re
from typing import Any, Dict

from llm_shield_proxy.engines.stateless_mutation_engine.crypto import StatelessPIICipher


class StatelessStreamingLexer:
    """
    Bounded-lookahead streaming lexer for v3 PII rehydration.
    Handles fragmented chunks while retaining a fixed trailing window.
    """
    def __init__(self, cipher: StatelessPIICipher):
        self.cipher = cipher
        self.buffer = io.StringIO()
        self.pending_rehydrations: Dict[str, str] = {}

        # Regex patterns to detect _ctx_hash_<prop> and array proxy objects natively in streams
        self.ctx_hash_pattern = re.compile(r'"_ctx_hash_([^"]+)"\s*:\s*"([^"]*)"\s*,?')
        self.shield_proxy_pattern = re.compile(r'\{\s*"_shield_val"\s*:\s*"([^"]*)"\s*,\s*"_shield_ctx"\s*:\s*"([^"]*)"\s*\}')
        self.property_pattern = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')

    # Pending property rehydrations kept across chunks.
    MAX_PENDING = 4096

    @staticmethod
    def _max_buffer() -> int:
        from llm_shield_proxy.core.config import settings

        return int(settings.MAX_SSE_LINE_LENGTH)

    def feed_chunk(self, chunk: str) -> str:
        """
        Ingests a chunk of the stream, processes closed JSON sibling pairs,
        prunes the context hashes, and yields safe rehydrated text.

        Linear in the chunk: each pattern is applied in one left-to-right pass.
        A buffer larger than MAX_SSE_LINE_LENGTH raises StreamCapacityExceeded
        (fail closed), matching the SSE path's line cap.
        """
        self.buffer.seek(0, io.SEEK_END)
        if self.buffer.tell() + len(chunk) > self._max_buffer():
            from llm_shield_proxy.streaming.streaming import StreamCapacityExceeded

            raise StreamCapacityExceeded("v3 stream chunk exceeded maximum safe length")
        self.buffer.write(chunk)
        content = self.buffer.getvalue()

        # 1. Prune and Rehydrate _ctx_hash_<prop> key-value pairs
        def _prune(match: "re.Match[str]") -> str:
            prop = match.group(1)
            token = match.group(2)
            pt = self.cipher.decrypt(token, prop)
            if pt != "[CORRUPTED]":
                self.pending_rehydrations.pop(prop, None)
                self.pending_rehydrations[prop] = pt
            # Remove the _ctx_hash sibling from the buffer
            return ""

        content = self.ctx_hash_pattern.sub(_prune, content)
        # Bounded: an upstream that names props it never sends cannot grow this forever.
        while len(self.pending_rehydrations) > self.MAX_PENDING:
            del self.pending_rehydrations[next(iter(self.pending_rehydrations))]

        # Clean any orphaned leading or duplicate commas caused by chunk boundaries
        content = re.sub(r'\{\s*,', '{', content)
        content = re.sub(r',\s*,', ', ', content)
        content = re.sub(r',\s*\}', '}', content)

        # 2. Rehydrate the corresponding actual properties
        # `prop` is an upstream-chosen JSON property name, captured by
        # `"_ctx_hash_([^"]+)"`, and `[^"]+` admits every regex metacharacter, so it is
        # never compiled as a pattern. One pass over every `"key": "value"` pair
        # replaces the first occurrence of each pending property (it used to be one
        # full search per pending property).
        if self.pending_rehydrations:
            def _restore(match: "re.Match[str]") -> str:
                prop = match.group(1)
                pt = self.pending_rehydrations.pop(prop, None)
                if pt is None:
                    return match.group(0)
                return f'"{prop}": {json.dumps(pt)}'

            content = self.property_pattern.sub(_restore, content)

        # 3. Prune and Rehydrate array proxy objects
        def _proxy(match: "re.Match[str]") -> str:
            fake_val = match.group(1)
            token = match.group(2)

            # The AAD context for array proxies is the fake value itself or empty.
            # Assuming AAD is empty string or fake_val. We will try empty first.
            pt = self.cipher.decrypt(token, "")
            if pt == "[CORRUPTED]":
                pt = self.cipher.decrypt(token, fake_val)
            return json.dumps(pt if pt != "[CORRUPTED]" else fake_val)

        content = self.shield_proxy_pattern.sub(_proxy, content)

        # 4. Flush safe buffer
        # We retain up to 256 characters of the trailing buffer to avoid breaking JSON keys across chunks.
        # If the buffer is smaller than 256, we don't emit yet.

        # Defend against whitespace padding attacks by stripping excessive trailing whitespace
        # before computing the flush point, keeping memory strictly bounded.
        content = re.sub(r'\s{256,}', ' ', content)

        flush_point = max(0, len(content) - 256)

        self.buffer = io.StringIO()
        if flush_point > 0:
            emitted = content[:flush_point]
            self.buffer.write(content[flush_point:])
            return emitted

        self.buffer.write(content)
        return ""

    def flush(self) -> str:
        """
        Emits any remaining buffer contents at the end of the stream.
        """
        # Final cleanup of any orphaned commas before emitting
        content = self.buffer.getvalue()
        content = re.sub(r'\{\s*,', '{', content)
        content = re.sub(r',\s*,', ', ', content)
        content = re.sub(r',\s*\}', '}', content)
        self.buffer = io.StringIO()
        return content

class NonStreamingRehydrator:
    """
    Non-recursive iterative JSON stack rehydrator for complete payloads.
    Stack use grows with the number of nested containers up to the depth limit.
    """
    def __init__(self, cipher: StatelessPIICipher):
        self.cipher = cipher

    def rehydrate(self, payload: Any) -> Any:
        if not isinstance(payload, (dict, list)):
            return payload

        stack: list[tuple[Any, int]] = [(payload, 0)]

        while stack:
            curr, depth = stack.pop()
            if depth > 40:
                raise ValueError("AST Depth Exceeded")

            if isinstance(curr, dict):
                # Check for array proxy object
                if "_shield_val" in curr and "_shield_ctx" in curr:
                    pt = self.cipher.decrypt(curr["_shield_ctx"], "")
                    if pt == "[CORRUPTED]":
                        pt = self.cipher.decrypt(curr["_shield_ctx"], curr["_shield_val"])
                    curr["_shield_val"] = pt if pt != "[CORRUPTED]" else curr["_shield_val"]
                    continue

                keys_to_remove = []
                rehydrations = {}

                for k, v in curr.items():
                    if isinstance(v, (dict, list)):
                        stack.append((v, depth + 1))
                    elif isinstance(k, str) and k.startswith("_ctx_hash_"):
                        prop = k[10:]
                        pt = self.cipher.decrypt(v, prop)
                        if pt != "[CORRUPTED]":
                            rehydrations[prop] = pt
                        keys_to_remove.append(k)

                for k in keys_to_remove:
                    del curr[k]

                for prop, pt in rehydrations.items():
                    if prop in curr:
                        curr[prop] = pt

            elif isinstance(curr, list):
                # Rehydrate array items
                for i in range(len(curr)):
                    item = curr[i]
                    if isinstance(item, dict) and "_shield_val" in item and "_shield_ctx" in item:
                        pt = self.cipher.decrypt(item["_shield_ctx"], "")
                        if pt == "[CORRUPTED]":
                            pt = self.cipher.decrypt(item["_shield_ctx"], item["_shield_val"])
                        curr[i] = pt if pt != "[CORRUPTED]" else item["_shield_val"]
                    elif isinstance(item, (dict, list)):
                        stack.append((item, depth + 1))

        return payload
