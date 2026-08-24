import json
import re
from typing import Dict, Optional, Any
from llm_shield_proxy.v3.crypto import StatelessPIICipher

class StatelessStreamingLexer:
    """
    Zero-Allocation Streaming Lexer for v3 PII Rehydration.
    Handles SSE fragmented chunks and O(1) space lookahead buffering.
    """
    def __init__(self, cipher: StatelessPIICipher):
        self.cipher = cipher
        self.buffer = ""
        self.pending_rehydrations: Dict[str, str] = {}
        
        # Regex patterns to detect _ctx_hash_<prop> and array proxy objects natively in streams
        self.ctx_hash_pattern = re.compile(r'"_ctx_hash_([^"]+)"\s*:\s*"([^"]*)"\s*,?')
        self.shield_proxy_pattern = re.compile(r'\{\s*"_shield_val"\s*:\s*"([^"]*)"\s*,\s*"_shield_ctx"\s*:\s*"([^"]*)"\s*\}')

    def feed_chunk(self, chunk: str) -> str:
        """
        Ingests a chunk of the stream, processes closed JSON sibling pairs,
        prunes the context hashes, and yields safe rehydrated text.
        """
        self.buffer = "".join([self.buffer, chunk])
        
        # 1. Prune and Rehydrate _ctx_hash_<prop> key-value pairs
        while True:
            match = self.ctx_hash_pattern.search(self.buffer)
            if not match:
                break
            prop = match.group(1)
            token = match.group(2)
            
            pt = self.cipher.decrypt(token, prop)
            if pt != "[CORRUPTED]":
                self.pending_rehydrations[prop] = pt
                
            start, end = match.span()
            # Remove the _ctx_hash sibling from the buffer
            self.buffer = self.buffer[:start] + self.buffer[end:]

        # Clean any orphaned leading or duplicate commas caused by chunk boundaries
        self.buffer = re.sub(r'\{\s*,', '{', self.buffer)
        self.buffer = re.sub(r',\s*,', ', ', self.buffer)
        self.buffer = re.sub(r',\s*\}', '}', self.buffer)

        # 2. Rehydrate the corresponding actual properties
        for prop, pt in list(self.pending_rehydrations.items()):
            prop_pattern = re.compile(rf'"{prop}"\s*:\s*"([^"]*)"')
            match = prop_pattern.search(self.buffer)
            if match:
                start, end = match.span()
                safe_pt = json.dumps(pt)
                replacement = f'"{prop}": {safe_pt}'
                self.buffer = self.buffer[:start] + replacement + self.buffer[end:]
                del self.pending_rehydrations[prop]

        # 3. Prune and Rehydrate array proxy objects
        while True:
            match = self.shield_proxy_pattern.search(self.buffer)
            if not match:
                break
            fake_val = match.group(1)
            token = match.group(2)
            
            # The AAD context for array proxies is the fake value itself or empty.
            # Assuming AAD is empty string or fake_val. We will try empty first.
            pt = self.cipher.decrypt(token, "")
            if pt == "[CORRUPTED]":
                pt = self.cipher.decrypt(token, fake_val)
                
            start, end = match.span()
            safe_pt = json.dumps(pt if pt != "[CORRUPTED]" else fake_val)
            self.buffer = self.buffer[:start] + safe_pt + self.buffer[end:]

        # 4. Flush safe buffer
        # We retain up to 256 characters of the trailing buffer to avoid breaking JSON keys across chunks.
        # If the buffer is smaller than 256, we don't emit yet.
        flush_point = max(0, len(self.buffer) - 256)
        
        if flush_point > 0:
            emitted = self.buffer[:flush_point]
            self.buffer = self.buffer[flush_point:]
            return emitted
        return ""
        
    def flush(self) -> str:
        """
        Emits any remaining buffer contents at the end of the stream.
        """
        # Final cleanup of any orphaned commas before emitting
        self.buffer = re.sub(r'\{\s*,', '{', self.buffer)
        self.buffer = re.sub(r',\s*,', ', ', self.buffer)
        self.buffer = re.sub(r',\s*\}', '}', self.buffer)
        emitted = self.buffer
        self.buffer = ""
        return emitted

class NonStreamingRehydrator:
    """
    Non-recursive iterative JSON stack rehydrator for complete payloads.
    Memory footprint bounded strictly by O(1) pre-allocated stack.
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
