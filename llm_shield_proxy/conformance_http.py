"""Endpoint-neutral HTTP profile for streaming privacy gateway evaluations."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

PROTECTED_VALUES = {
    "EMAIL": "person@example.invalid",
    "SSN": "123-45-6789",
    "CREDIT_CARD": "4532-1234-5678-9012",
}
PROMPT = (
    "Synthetic conformance record: contact person@example.invalid, "
    "SSN 123-45-6789, card 4532-1234-5678-9012"
)


def _timestamp() -> str:
    source_epoch = os.getenv("SOURCE_DATE_EPOCH")
    current = (
        datetime.fromtimestamp(int(source_epoch), tz=timezone.utc)
        if source_epoch is not None
        else datetime.now(timezone.utc)
    )
    return current.isoformat().replace("+00:00", "Z")


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    def at(fraction: float) -> float:
        return ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)]

    return {
        "mean": statistics.fmean(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
    }


class _CaptureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[bytes] = []

    def append(self, body: bytes) -> None:
        with self.lock:
            self.requests.append(body)

    def snapshot(self) -> list[bytes]:
        with self.lock:
            return list(self.requests)


def _handler_for(state: _CaptureState):
    class CaptureHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/v1/models":
                body = json.dumps(
                    {"object": "list", "data": [{"id": "conformance-model", "object": "model"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            state.append(body)
            try:
                payload = json.loads(body)
                messages = payload.get("messages", [])
                content = messages[-1].get("content", "") if messages else ""
                if not isinstance(content, str):
                    content = json.dumps(content, separators=(",", ":"))
            except (AttributeError, json.JSONDecodeError, TypeError):
                self.send_error(400)
                return

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            for character in content:
                event = {"choices": [{"delta": {"content": character}}]}
                line = f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return CaptureHandler


async def _exercise_target(
    target_base_url: str,
    api_key: str,
    model: str,
    iterations: int,
    timeout_seconds: float,
    extra_headers: dict[str, str],
) -> dict[str, Any]:
    url = urljoin(target_base_url.rstrip("/") + "/", "chat/completions")
    headers = {"authorization": f"Bearer {api_key}", **extra_headers}
    durations_ms: list[float] = []
    final_text = ""
    final_events = 0
    final_invalid_events = 0
    final_done_markers = 0
    final_content_type = ""
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for iteration in range(iterations):
            started = time.perf_counter()
            reconstructed: list[str] = []
            event_count = 0
            invalid_events = 0
            done_markers = 0
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers={**headers, "x-session-id": f"conformance-{iteration}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": PROMPT}],
                        "stream": True,
                    },
                ) as response:
                    final_content_type = response.headers.get("content-type", "")
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done_markers += 1
                            continue
                        try:
                            event = json.loads(data)
                            value = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if isinstance(value, str):
                                reconstructed.append(value)
                            event_count += 1
                        except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
                            invalid_events += 1
                durations_ms.append((time.perf_counter() - started) * 1_000)
                final_text = "".join(reconstructed)
                final_events = event_count
                final_invalid_events = invalid_events
                final_done_markers = done_markers
            except (httpx.HTTPError, UnicodeError) as exc:
                errors.append(type(exc).__name__)

    return {
        "durations_ms": durations_ms,
        "text_matches": final_text == PROMPT,
        "events": final_events,
        "invalid_events": final_invalid_events,
        "done_markers": final_done_markers,
        "content_type": final_content_type,
        "errors": errors,
    }


def run_http_conformance(
    target_base_url: str,
    *,
    api_key: str = "conformance-key",
    model: str = "conformance-model",
    implementation_name: str = "external-openai-compatible-endpoint",
    implementation_version: str = "unspecified",
    iterations: int = 3,
    timeout_seconds: float = 30.0,
    capture_host: str = "127.0.0.1",
    capture_port: int = 8765,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Evaluate an OpenAI-compatible endpoint against a controlled capture upstream.

    Configure the target gateway's upstream base URL to the capture server before running.
    Use ``capture://self`` as the target to record an explicit raw-pass-through baseline.
    """
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    state = _CaptureState()
    server = ThreadingHTTPServer((capture_host, capture_port), _handler_for(state))
    actual_host, actual_port = server.server_address[:2]
    capture_base_url = f"http://{actual_host}:{actual_port}/v1"
    effective_target = capture_base_url if target_base_url == "capture://self" else target_base_url
    thread = threading.Thread(target=server.serve_forever, name="conformance-capture", daemon=True)
    thread.start()
    try:
        exercise = asyncio.run(
            _exercise_target(
                effective_target,
                api_key,
                model,
                iterations,
                timeout_seconds,
                extra_headers or {},
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    captured = state.snapshot()
    leaked_types = [
        entity
        for entity, value in PROTECTED_VALUES.items()
        if any(value.encode("utf-8") in request_body for request_body in captured)
    ]
    latency = _percentiles(exercise["durations_ms"])
    checks = {
        "configured_upstream_boundary": {
            "passed": bool(captured) and not leaked_types,
            "captured_requests": len(captured),
            "leaked_entity_types": leaked_types,
            "payload_content_included": False,
        },
        "fragmentation_safety": {
            "passed": exercise["text_matches"] and exercise["events"] > 1,
            "one_character_events_requested": True,
            "events_observed": exercise["events"],
        },
        "sse_validity": {
            "passed": (
                exercise["invalid_events"] == 0
                and exercise["done_markers"] == 1
                and "text/event-stream" in exercise["content_type"].lower()
                and not exercise["errors"]
            ),
            "invalid_events": exercise["invalid_events"],
            "done_markers": exercise["done_markers"],
            "errors": exercise["errors"],
        },
        "response_fidelity": {
            "passed": exercise["text_matches"],
            "expected_value_reconstructed": exercise["text_matches"],
            "payload_content_included": False,
        },
        "client_observed_latency": {
            "passed": len(exercise["durations_ms"]) == iterations,
            "threshold_enforced": False,
            "unit": "milliseconds",
            "iterations": iterations,
            **latency,
        },
    }
    return {
        "schema": "llm-shield.streaming-privacy-http-profile/v1.0.0",
        "generated_at": _timestamp(),
        "profile": {
            "name": "OpenAI-compatible HTTP gateway profile",
            "scope": "client-to-gateway request, controlled configured-upstream capture, and SSE response",
        },
        "implementation": {"name": implementation_name, "version": implementation_version},
        "harness_revision": os.getenv("GITHUB_SHA") or os.getenv("LLM_SHIELD_SOURCE_REVISION") or "unknown",
        "environment": {
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "capture": {
            "bind_host": capture_host,
            "port": actual_port,
            "target_must_be_preconfigured_for": capture_base_url,
        },
        "checks": checks,
        "passed": all(check["passed"] for check in checks.values()),
        "limitations": [
            "The target must be configured to use the harness capture server as its upstream.",
            "This HTTP profile does not evaluate gateway process RSS, audit evidence, or public-model behavior.",
            "The synthetic fixture does not establish population-level detector accuracy.",
            "Client-observed latency includes local HTTP and capture-server work and has no universal threshold.",
        ],
    }
