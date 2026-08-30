"""Reproducible local conformance and microbenchmark harness.

The harness deliberately avoids public model APIs. It measures only local
engine/buffer work and labels that scope in its output.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from llm_shield_proxy.compliance.report import verify_worm_log
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer, rehydrate_sse_stream


def _package_version() -> str:
    try:
        return version("llm-shield-proxy")
    except PackageNotFoundError:
        return "source"


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


def _time_ns(operation: Callable[[], Any], iterations: int) -> dict[str, float]:
    # Untimed warmup keeps import, allocator, and first-call effects out of the sample.
    for _ in range(min(100, max(10, iterations // 10))):
        operation()
    measurements: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        measurements.append(float(time.perf_counter_ns() - started))
    return _percentiles(measurements)


def _fragmentation_conformance() -> dict[str, Any]:
    placeholder = "[EMAIL_1]"
    original = "person@example.invalid"
    failures: list[dict[str, Any]] = []
    partitions_tested = 0

    for split in range(len(placeholder) + 1):
        vault = Vault(synthetic=False)
        vault.token_to_original[placeholder] = original
        vault.original_to_token[original] = placeholder
        vault.max_token_length = len(placeholder)
        buffer = SSERehydrationBuffer(vault)
        output = buffer.process_delta_text(placeholder[:split])
        output += buffer.process_delta_text(placeholder[split:] + "!")
        output += buffer.process_delta_text("", is_final=True)
        partitions_tested += 1
        if output != original + "!" or placeholder in output:
            failures.append({"partition": split, "reason": "incorrect_rehydration"})

    vault = Vault(synthetic=False)
    vault.token_to_original[placeholder] = original
    vault.original_to_token[original] = placeholder
    vault.max_token_length = len(placeholder)
    buffer = SSERehydrationBuffer(vault)
    character_output = "".join(buffer.process_delta_text(character) for character in placeholder + "!")
    character_output += buffer.process_delta_text("", is_final=True)
    partitions_tested += 1
    if character_output != original + "!":
        failures.append({"partition": "one_character", "reason": "incorrect_rehydration"})

    return {
        "passed": not failures,
        "partitions_tested": partitions_tested,
        "failures": failures,
        "invariant": "protected placeholder fragments are retained until exact rehydration is possible",
    }


async def _sse_integration_conformance() -> dict[str, dict[str, Any]]:
    from llm_shield_proxy.observability.audit import audit_logger

    placeholder = "[EMAIL_1]"
    original = "person@example.invalid"
    vault = Vault(synthetic=False)
    vault.token_to_original[placeholder] = original
    vault.original_to_token[original] = placeholder
    vault.max_token_length = len(placeholder)

    async def stream():
        for fragment in ["[EM", "AIL_1]", "!"]:
            payload = {"choices": [{"delta": {"content": fragment}}]}
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    async def utf8_split_stream():
        payload = json.dumps({"choices": [{"delta": {"content": "caf\u00e9"}}]}, ensure_ascii=False)
        encoded = f"data: {payload}\n\ndata: [DONE]\n\n".encode("utf-8")
        split_at = encoded.index("é".encode("utf-8")) + 1
        yield encoded[:split_at]
        yield encoded[split_at:]

    # The production stream emits a signed receipt at finalization. The harness
    # validates stream behavior without polluting its stdout or an operator sink.
    audit_was_disabled = audit_logger.disabled
    audit_logger.disabled = True
    try:
        output = b"".join([chunk async for chunk in rehydrate_sse_stream(stream(), vault)]).decode("utf-8")
        utf8_output = b"".join(
            [chunk async for chunk in rehydrate_sse_stream(utf8_split_stream(), Vault(synthetic=False))]
        ).decode("utf-8")
    finally:
        audit_logger.disabled = audit_was_disabled
    data_lines = [line[5:].strip() for line in output.splitlines() if line.startswith("data:")]
    json_events: list[dict[str, Any]] = []
    invalid_json_events = 0
    for data in data_lines:
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
            if isinstance(event, dict):
                json_events.append(event)
            else:
                invalid_json_events += 1
        except json.JSONDecodeError:
            invalid_json_events += 1

    reconstructed = "".join(
        str(event.get("choices", [{}])[0].get("delta", {}).get("content", ""))
        for event in json_events
        if event.get("choices")
    )
    done_markers = sum(data == "[DONE]" for data in data_lines)
    return {
        "sse_validity": {
            "passed": (
                invalid_json_events == 0
                and done_markers == 1
                and output.endswith("\n\n")
                and "caf\u00e9" in utf8_output
            ),
            "provider_shape": "OpenAI-compatible SSE",
            "json_events_checked": len(json_events),
            "invalid_json_events": invalid_json_events,
            "done_markers": done_markers,
            "stream_terminated_with_blank_line": output.endswith("\n\n"),
            "utf8_mid_codepoint_split_preserved": "caf\u00e9" in utf8_output,
        },
        "rehydration_fidelity": {
            "passed": reconstructed == original + "!" and placeholder not in output,
            "expected_value_reconstructed": reconstructed == original + "!",
            "protected_placeholder_present": placeholder in output,
            "payload_content_included": False,
        },
    }


def _egress_conformance() -> dict[str, Any]:
    protected_values = {
        "EMAIL": "person@example.invalid",
        "SSN": "123-45-6789",
        "CREDIT_CARD": "4532-1234-5678-9012",
    }
    source = {
        "model": "local-conformance",
        "messages": [
            {
                "role": "user",
                "content": "Contact person@example.invalid, SSN 123-45-6789, card 4532-1234-5678-9012",
            }
        ],
    }
    engine = PIIEngine(enable_tier2=False, enable_tier3=False)
    vault = Vault(synthetic=False)
    transformed = engine.redact_payload(source, vault)
    upstream_bytes = json.dumps(transformed, sort_keys=True).encode("utf-8")
    leaks = [entity for entity, value in protected_values.items() if value.encode("utf-8") in upstream_bytes]
    detected = dict(sorted(vault.type_counters.items()))
    vault.original_to_token.clear()
    vault.token_to_original.clear()
    return {
        "passed": not leaks and all(detected.get(entity, 0) >= 1 for entity in protected_values),
        "boundary": "serialized payload presented to the configured upstream",
        "protected_entity_types": sorted(protected_values),
        "detected_entity_counts": detected,
        "leaked_entity_types": leaks,
        "payload_content_included": False,
    }


def _audit_integrity_conformance() -> dict[str, Any]:
    """Exercise audit continuity/signature verification and a tamper-negative control."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    fingerprint = hashlib.sha256(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).hexdigest()

    previous_hash = "0" * 64
    records: list[dict[str, Any]] = []
    for sequence in range(1, 3):
        base_record = {
            "chain_id": "conformance-chain",
            "event": "CONFORMANCE_AUDIT_EVENT",
            "previous_hash": previous_hash,
            "sequence": sequence,
            "severity": "INFO",
            "timestamp": f"2026-01-01T00:00:0{sequence}Z",
        }
        canonical_event = json.dumps(base_record, sort_keys=True)
        record_hash = hashlib.sha256(canonical_event.encode("utf-8")).hexdigest()
        hashed_record = f'{canonical_event[:-1]}, "hash": "{record_hash}"}}'
        signature = base64.b64encode(private_key.sign(hashed_record.encode("utf-8"))).decode("ascii")
        signed_record = (
            f'{hashed_record[:-1]}, "signature": "{signature}", '
            f'"public_key_fingerprint": "{fingerprint}"}}'
        )
        records.append(json.loads(signed_record))
        previous_hash = record_hash

    with tempfile.TemporaryDirectory(prefix="llm-shield-conformance-") as directory:
        path = Path(directory) / "audit.jsonl"
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        valid = verify_worm_log(str(path), public_pem)

        tampered = [dict(record) for record in records]
        tampered[1]["event"] = "TAMPERED_EVENT"
        path.write_text("\n".join(json.dumps(record) for record in tampered) + "\n", encoding="utf-8")
        invalid = verify_worm_log(str(path), public_pem)

    passed = (
        valid["chain_valid"] is True
        and valid["signatures_valid"] == 2
        and valid["first_sequence"] == 1
        and valid["last_sequence"] == 2
        and invalid["chain_valid"] is False
    )
    return {
        "passed": passed,
        "records_verified": valid["total_events"],
        "signatures_verified": valid["signatures_valid"],
        "sequence_continuity_verified": valid["chain_valid"] is True,
        "tamper_negative_control_detected": invalid["chain_valid"] is False,
        "record_content_included": False,
    }


def _microbenchmarks(iterations: int) -> dict[str, Any]:
    empty_vault = Vault(synthetic=False)
    empty_buffer = SSERehydrationBuffer(empty_vault)
    no_op = _time_ns(lambda: "ordinary stream content", iterations)
    empty_path = _time_ns(lambda: empty_buffer.process_delta_text("ordinary stream content"), iterations)

    protected_vault = Vault(synthetic=False)
    protected_vault.token_to_original["[EMAIL_1]"] = "person@example.invalid"
    protected_vault.original_to_token["person@example.invalid"] = "[EMAIL_1]"
    protected_vault.max_token_length = len("[EMAIL_1]")
    protected_buffer = SSERehydrationBuffer(protected_vault)
    protected_path = _time_ns(lambda: protected_buffer.process_delta_text("[EMAIL_1] "), iterations)

    return {
        "scope": "in-process Python operations; excludes ASGI, HTTP, TLS, upstream, and model latency",
        "unit": "nanoseconds",
        "iterations": iterations,
        "no_op": no_op,
        "empty_vault_buffer": empty_path,
        "protected_token_buffer": protected_path,
    }


def _memory_check(iterations: int) -> dict[str, Any]:
    vault = Vault(synthetic=False)
    buffer = SSERehydrationBuffer(vault)
    tracemalloc.start()
    for _ in range(iterations):
        buffer.process_delta_text("ordinary stream content")
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    protected_vault = Vault(synthetic=False)
    protected_vault.token_to_original["[EMAIL_1]"] = "person@example.invalid"
    protected_vault.original_to_token["person@example.invalid"] = "[EMAIL_1]"
    protected_buffer = SSERehydrationBuffer(protected_vault)
    protected_buffer.process_delta_text("[EMAI")
    retained_characters = len(protected_buffer.content_buffer)
    return {
        "scope": "Python allocations during empty-vault buffer processing; not process RSS",
        "iterations": iterations,
        "current_bytes": current,
        "peak_bytes": peak,
        "retained_characters": retained_characters,
        "retention_bound_characters": len("[EMAIL_1]") - 1,
    }


def run_conformance(iterations: int = 2_000) -> dict[str, Any]:
    """Run deterministic correctness checks and labeled local microbenchmarks."""
    if iterations < 10:
        raise ValueError("iterations must be at least 10")
    sse_checks = asyncio.run(_sse_integration_conformance())
    microbenchmarks = _microbenchmarks(iterations)
    memory = _memory_check(iterations)
    latency_values = [
        value
        for operation in ("no_op", "empty_vault_buffer", "protected_token_buffer")
        for value in microbenchmarks[operation].values()
    ]
    checks = {
        "fragmentation_safety": _fragmentation_conformance(),
        "raw_pii_egress": _egress_conformance(),
        "sse_validity": sse_checks["sse_validity"],
        "rehydration_fidelity": sse_checks["rehydration_fidelity"],
        "audit_integrity": _audit_integrity_conformance(),
        "latency_measurement": {
            "passed": all(value >= 0 for value in latency_values),
            "threshold_enforced": False,
            "samples_per_operation": iterations,
            "scope": microbenchmarks["scope"],
        },
        "memory_bounded": {
            "passed": memory["retained_characters"] <= memory["retention_bound_characters"],
            "rss_threshold_enforced": False,
            "allocation_measurement_recorded": True,
            "retention_bound_verified": True,
        },
    }
    return {
        "schema": "llm-shield.streaming-privacy-conformance/v1.0.0",
        "specification": {
            "name": "Streaming Privacy Gateway Conformance Specification",
            "version": "1.0.0",
            "href": "https://project-0039f5fd-ac66-4a1c-9e0.web.app/docs/conformance/specification-v1",
        },
        "generated_at": _timestamp(),
        "implementation": {"name": "llm-shield-proxy", "version": _package_version()},
        "source_revision": os.getenv("GITHUB_SHA") or os.getenv("LLM_SHIELD_SOURCE_REVISION") or "unknown",
        "environment": {
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "checks": checks,
        "microbenchmarks": microbenchmarks,
        "memory": memory,
        "passed": all(check["passed"] for check in checks.values()),
        "limitations": [
            "This local harness is not an end-to-end proxy throughput benchmark.",
            "Synthetic vectors do not establish population-level PII detection accuracy.",
            "Results from shared or power-managed hosts should not be compared as controlled measurements.",
        ],
    }


def write_conformance_report(report: dict[str, Any], output_path: str) -> str:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(destination)
