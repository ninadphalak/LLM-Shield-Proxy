"""The shim must fail closed, and must not become a cheaper way to reach the vault.

No LiteLLM and no Shield are needed: the shim is driven directly through FastAPI's
TestClient with its Shield transport stubbed. What is asserted here is the security
posture, not the HTTP plumbing -- a guardrail that returns ``NONE`` when it cannot
do its job is worse than an absent one, because the caller believes the data was
redacted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIM_PATH = REPO_ROOT / "examples" / "integrations" / "litellm" / "litellm_guardrail_shim.py"

SHIM_KEY = "shim-key-for-tests"
ENDPOINT = "/beta/litellm_basic_guardrail_api"


def _load(monkeypatch, shim_key: str = SHIM_KEY):
    """Loads a fresh copy of the shim with the environment it reads at import time."""
    monkeypatch.setenv("LITELLM_GUARDRAIL_SHIM_KEY", shim_key)
    monkeypatch.setenv("SHIELD_API_KEY", "shield-key-for-tests")
    monkeypatch.setenv("SHIELD_BASE_URL", "http://shield.invalid")
    name = "litellm_guardrail_shim_under_test"
    spec = importlib.util.spec_from_file_location(name, SHIM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _post(client, payload, key=SHIM_KEY):
    headers = {"x-api-key": key} if key is not None else {}
    return client.post(ENDPOINT, headers=headers, json=payload)


def _body(input_type="request", texts=None, call_id="call-1"):
    return {
        "texts": ["a@b.com"] if texts is None else texts,
        "input_type": input_type,
        "litellm_call_id": call_id,
    }


# --- authentication ---------------------------------------------------------


def test_missing_key_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    assert _post(TestClient(module.app), _body(), key=None).status_code == 401


def test_wrong_key_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    assert _post(TestClient(module.app), _body(), key="not-the-key").status_code == 401


def test_unset_shim_key_refuses_everything(monkeypatch):
    """An unset secret must never mean "open"."""
    module = _load(monkeypatch, shim_key="")
    assert _post(TestClient(module.app), _body(), key="").status_code == 401


# --- bounds -----------------------------------------------------------------


def test_too_many_texts_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    texts = ["x"] * (module.MAX_TEXTS_PER_REQUEST + 1)
    assert _post(TestClient(module.app), _body(texts=texts)).status_code == 413


def test_oversized_payload_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    texts = ["x" * (module.MAX_TOTAL_TEXT_CHARS + 1)]
    assert _post(TestClient(module.app), _body(texts=texts)).status_code == 413


# --- translation ------------------------------------------------------------


def test_request_side_redacts_via_the_redact_endpoint(monkeypatch):
    module = _load(monkeypatch)
    seen = {}

    async def fake(path, session_id, payload):
        seen.update(path=path, session=session_id, payload=payload)
        return {"texts": ["<EMAIL_ADDRESS>"]}

    monkeypatch.setattr(module, "_call_shield", fake)
    response = _post(TestClient(module.app), _body("request", ["a@b.com"], "call-1"))

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "GUARDRAIL_INTERVENED"
    assert body["texts"] == ["<EMAIL_ADDRESS>"]
    assert seen["path"] == "/v1/guard/redact"
    assert seen["session"] == "litellm-call-1"
    assert seen["payload"] == {"texts": ["a@b.com"]}


def test_response_side_restores_via_the_rehydrate_endpoint(monkeypatch):
    module = _load(monkeypatch)
    seen = {}

    async def fake(path, session_id, payload):
        seen.update(path=path, session=session_id)
        return {"texts": ["a@b.com"]}

    monkeypatch.setattr(module, "_call_shield", fake)
    body = _post(TestClient(module.app), _body("response", ["<EMAIL_ADDRESS>"], "call-2")).json()

    assert body["action"] == "GUARDRAIL_INTERVENED"
    assert body["texts"] == ["a@b.com"]
    assert seen["path"] == "/v1/guard/rehydrate"
    assert seen["session"] == "litellm-call-2"


def test_unchanged_texts_report_none(monkeypatch):
    module = _load(monkeypatch)

    async def fake(path, session_id, payload):
        return {"texts": ["nothing to redact"]}

    monkeypatch.setattr(module, "_call_shield", fake)
    body = _post(TestClient(module.app), _body("request", ["nothing to redact"])).json()

    assert body["action"] == "NONE"


# --- fail closed ------------------------------------------------------------


def test_shield_failure_blocks_rather_than_passing_through(monkeypatch):
    module = _load(monkeypatch)

    async def fake(path, session_id, payload):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(module, "_call_shield", fake)
    body = _post(TestClient(module.app), _body()).json()

    assert body["action"] == "BLOCKED"
    assert "unreachable" in body["blocked_reason"]


def test_missing_call_id_blocks(monkeypatch):
    """Without a call id the reply cannot be restored, so placeholders must not be promised."""
    module = _load(monkeypatch)
    body = _post(TestClient(module.app), _body(call_id=None)).json()

    assert body["action"] == "BLOCKED"
    assert "litellm_call_id" in body["blocked_reason"]


def test_overlong_call_id_blocks(monkeypatch):
    module = _load(monkeypatch)
    body = _post(TestClient(module.app), _body(call_id="c" * (module.MAX_SESSION_ID_CHARS + 1))).json()
    assert body["action"] == "BLOCKED"


@pytest.mark.parametrize(
    "bad",
    [
        {"texts": "not-a-list"},
        {"texts": ["only-one", "two"]},
        {"texts": [1, 2]},
        {},
    ],
)
def test_unusable_shield_payload_blocks(monkeypatch, bad):
    module = _load(monkeypatch)

    async def fake(path, session_id, payload):
        return bad

    monkeypatch.setattr(module, "_call_shield", fake)
    assert _post(TestClient(module.app), _body()).json()["action"] == "BLOCKED"
