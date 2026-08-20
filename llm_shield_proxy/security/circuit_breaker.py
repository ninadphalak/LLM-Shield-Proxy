"""Composite Agent Loop Circuit Breaker for LLM-Shield-Proxy.

Detects and throttles autonomous agents caught in hallucination loops.
"""

import collections
import difflib
import hashlib
import json
import math
from dataclasses import dataclass, field

from cachetools import TTLCache

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import RedisVaultStore, vault_store


class CircuitBreakerTrippedException(Exception):
    """Exception raised when an agent loop is detected."""

    def __init__(self, message: str, consecutive_turns: int):
        super().__init__(message)
        self.consecutive_turns = consecutive_turns


@dataclass
class SessionMetrics:
    """Memory-safe state tracking for a single session."""

    entropy_history: collections.deque[float] = field(default_factory=lambda: collections.deque(maxlen=5))
    tool_call_hashes: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=5))
    consecutive_duplicate_count: int = 0


# LRU Cache mapping session_id to SessionMetrics
circuit_breaker_cache: TTLCache[str, SessionMetrics] = TTLCache(maxsize=10000, ttl=600)


def calculate_shannon_entropy(text: str) -> float:
    """Computes Shannon Token Entropy of the given text."""
    if not text:
        return 0.0
    probabilities = [float(text.count(c)) / len(text) for c in set(text)]
    return -sum(p * math.log2(p) for p in probabilities)


def extract_tool_call_signature_hash(payload: dict) -> str:
    """Quickly extracts and hashes tool_call signatures to detect identical actions."""
    signatures = []

    # We inspect the messages list to find recent assistant tool calls
    messages = payload.get("messages", [])
    if isinstance(messages, list) and messages:
        # Looking at the last few messages for tool calls
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict) and "function" in tc:
                            fn = tc["function"]
                            if isinstance(fn, dict):
                                name = fn.get("name", "")
                                args = fn.get("arguments", "")
                                signatures.append(f"{name}:{args}")
                break

    if not signatures:
        return ""

    # Sort and hash for stability
    combined_signature = "|".join(sorted(signatures))
    return hashlib.sha256(combined_signature.encode("utf-8")).hexdigest()[:16]


async def check_circuit_breaker(session_id: str, payload: dict) -> None:
    """
    Evaluates the payload against the session history to detect loops.
    Raises CircuitBreakerTrippedException if tripped.
    """
    if not session_id:
        return

    use_redis = isinstance(vault_store, RedisVaultStore)
    redis_key = f"circuit_breaker:{session_id}"

    if use_redis:
        data = await vault_store.async_client.get(redis_key)
        if data:
            try:
                metrics_dict = json.loads(data)
                metrics = SessionMetrics(
                    entropy_history=collections.deque(metrics_dict.get("entropy_history", []), maxlen=5),
                    tool_call_hashes=collections.deque(metrics_dict.get("tool_call_hashes", []), maxlen=5),
                    consecutive_duplicate_count=metrics_dict.get("consecutive_duplicate_count", 0),
                )
                setattr(metrics, "_last_bounded_payload", metrics_dict.get("_last_bounded_payload", ""))
            except (json.JSONDecodeError, TypeError):
                metrics = SessionMetrics()
        else:
            metrics = SessionMetrics()
    else:
        metrics = circuit_breaker_cache.get(session_id)
        if not metrics:
            metrics = SessionMetrics()
            circuit_breaker_cache[session_id] = metrics

    # Serialize payload to string, bounded to 4096 chars to prevent O(N^2) CPU spikes
    try:
        payload_str = json.dumps(payload)
    except (TypeError, ValueError):
        payload_str = str(payload)

    if len(payload_str) < 50:
        return

    bounded_payload_str = payload_str[:4096]

    current_entropy = calculate_shannon_entropy(bounded_payload_str)
    current_tool_hash = extract_tool_call_signature_hash(payload)

    is_duplicate = False

    # Check 1: Identical tool call hash (if present)
    if current_tool_hash and metrics.tool_call_hashes:
        if current_tool_hash == metrics.tool_call_hashes[-1]:
            is_duplicate = True

    # Check 2: Shannon Entropy & Lightweight String Similarity
    if not is_duplicate and metrics.entropy_history:
        prev_entropy = metrics.entropy_history[-1]
        delta_h = abs(current_entropy - prev_entropy)

        if delta_h < 0.05:
            # We must store a small piece of previous state to compare,
            # but since we avoid storing raw text across turns to save memory,
            # we rely primarily on the tool hash.
            # Since the user requested string similarity between consecutive turns,
            # we will store the last bounded payload directly in the dataclass as an exception
            # with strict bounds to maintain memory limits.
            last_payload_str = getattr(metrics, "_last_bounded_payload", "")
            if last_payload_str:
                matcher = difflib.SequenceMatcher(None, last_payload_str, bounded_payload_str)
                similarity = matcher.quick_ratio()
                if similarity > 0.95:
                    is_purely_numeric_diff = True
                    has_diff = False
                    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                        if tag in ("replace", "insert", "delete"):
                            has_diff = True
                            diff_chars1 = last_payload_str[i1:i2]
                            diff_chars2 = bounded_payload_str[j1:j2]
                            if (diff_chars1 and not diff_chars1.isdigit()) or (
                                diff_chars2 and not diff_chars2.isdigit()
                            ):
                                is_purely_numeric_diff = False
                                break

                    if not (has_diff and is_purely_numeric_diff):
                        is_duplicate = True

    if is_duplicate:
        metrics.consecutive_duplicate_count += 1
    else:
        # Reset if the loop breaks
        metrics.consecutive_duplicate_count = 0

    # Store state for next turn
    metrics.entropy_history.append(current_entropy)
    if current_tool_hash:
        metrics.tool_call_hashes.append(current_tool_hash)
    setattr(metrics, "_last_bounded_payload", bounded_payload_str)

    if use_redis:
        metrics_dict = {
            "entropy_history": list(metrics.entropy_history),
            "tool_call_hashes": list(metrics.tool_call_hashes),
            "consecutive_duplicate_count": metrics.consecutive_duplicate_count,
            "_last_bounded_payload": getattr(metrics, "_last_bounded_payload", ""),
        }
        async with vault_store.async_client.pipeline(transaction=False) as pipe:
            pipe.setex(redis_key, 600, json.dumps(metrics_dict))
            await pipe.execute()

    if metrics.consecutive_duplicate_count >= settings.AGENT_BREAKER_THRESHOLD - 1:
        # Reset count so they can try again after being tripped once?
        # For now, let it trip repeatedly if they keep sending the exact same payload.
        raise CircuitBreakerTrippedException("Agent loop detected", metrics.consecutive_duplicate_count + 1)
