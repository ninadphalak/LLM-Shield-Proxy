"""The gated LLM-Shield-Proxy rerun must publish its actual transport contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pii_leak_benchmark import fide_emitter

from benchmarks import fide_sweep

ROOT = Path(__file__).resolve().parents[2]


def _effective() -> dict[str, object]:
    return {
        "ENABLE_RESPONSE_PII_REDACTION": True,
        "ENABLE_RETRY_FAILOVER": True,
        "FALLBACK_BASE_URL": None,
        "HTTP_CONNECT_TIMEOUT_SECONDS": 10.0,
        "HTTP_TIMEOUT_SECONDS": 120.0,
        "MAX_RETRIES": 3,
        "SHIELD_FAILURE_MODE": "FAIL_CLOSED",
        "UPSTREAM_BASE_URL": "http://host.docker.internal:8799",
    }


def _contract(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    inspected = {
        "Config": {
            "Image": "shield-pypi:1.6.0",
            "Env": [
                "SHIELD_ENCRYPTION_KEY=must-not-appear",
                "OPENAI_API_KEY=must-not-appear",
            ],
        },
        "Image": "sha256:" + "a" * 64,
    }

    def fake_docker_json(command: str, *args: str) -> object:
        return [inspected] if command == "inspect" else _effective()

    monkeypatch.setattr(fide_sweep, "_docker_json", fake_docker_json)
    return fide_sweep._shield_transport_contract(60.0)


def test_driver_records_phase_timeouts_retry_image_and_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract(monkeypatch)
    assert contract["harness"] == {
        "library": "httpx",
        "overall_deadline_enforced": False,
        "connect_timeout_seconds": 5.0,
        "read_timeout_seconds": 60.0,
        "write_timeout_seconds": 60.0,
        "pool_timeout_seconds": 5.0,
    }
    target = contract["target"]
    assert target["retry"]["maximum_attempts"] == 4
    assert target["fallback"] == {
        "configured": False,
        "per_request_header_sent": False,
    }
    assert target["image"]["id"] == "sha256:" + "a" * 64
    assert target["effective_configuration"]["redacted_fields_present"] == [
        "OPENAI_API_KEY",
        "SHIELD_ENCRYPTION_KEY",
    ]
    serialized = json.dumps(contract)
    assert "must-not-appear" not in serialized


def test_contract_validates_against_the_v21_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (ROOT / "spec" / "v2.1.0" / "http-profile.schema.json").read_text(encoding="utf-8")
    )
    contract_schema = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        **schema["properties"]["transport_contract"],
    }
    jsonschema.Draft202012Validator(contract_schema).validate(_contract(monkeypatch))


def test_emitter_accepts_only_the_named_measurement_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract(monkeypatch)
    monkeypatch.setenv(fide_emitter.TRANSPORT_CONTRACT_ENV, json.dumps(contract))
    assert fide_emitter._transport_contract_from_environment() == contract

    contract["measurement_revision"] = "unreviewed"
    monkeypatch.setenv(fide_emitter.TRANSPORT_CONTRACT_ENV, json.dumps(contract))
    with pytest.raises(ValueError, match="measurement_revision"):
        fide_emitter._transport_contract_from_environment()
