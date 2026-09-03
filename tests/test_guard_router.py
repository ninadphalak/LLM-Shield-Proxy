"""Guard API Test Suite.

Covers the mask/unmask surface that third-party gateways (LiteLLM, Portkey,
Kong, Open WebUI) call when they own their own upstream request and cannot route
through the passthrough proxy.

The load-bearing case is `test_placeholder_split_across_deltas_is_not_leaked`: a
placeholder straddling an SSE chunk boundary must be held back rather than
emitted in fragments, while text before it is released immediately. Buffering
the whole stream would also avoid the leak, but would collapse first-token
latency into total-response latency, so the test asserts both properties.
"""

import pytest
from fastapi.testclient import TestClient

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)

SESSION = {"X-Session-ID": "guard-test-session", "Authorization": "Bearer dev-key"}
SECRET_EMAIL = "john.doe@example.com"
PROMPT = f"Email {SECRET_EMAIL} about it"


@pytest.fixture
def open_auth(monkeypatch):
    """Enables the explicit operator escape hatch used by the compose examples."""
    monkeypatch.setattr(settings, "OVERRIDE_CLIENT_AUTH", True)
    return None


def _redact(text: str, headers: dict = SESSION) -> str:
    response = client.post("/v1/guard/redact", headers=headers, json={"texts": [text]})
    assert response.status_code == 200, response.text
    return response.json()["texts"][0]


# --- Authentication (fail-closed) -------------------------------------------------


def test_redact_requires_authentication():
    response = client.post("/v1/guard/redact", json={"texts": ["anything"]})
    assert response.status_code == 401


def test_rehydrate_requires_authentication():
    """The rehydrate oracle returns plaintext PII and must never be open."""
    response = client.post("/v1/guard/rehydrate", json={"texts": ["anything"]})
    assert response.status_code == 401


def test_stream_rehydrate_requires_authentication():
    response = client.post("/v1/guard/rehydrate/stream", json={"text": "x", "carry": ""})
    assert response.status_code == 401


def test_provider_shaped_key_does_not_authenticate(monkeypatch):
    """A BYOK-shaped key is not authentication for this surface.

    The proxy path may forward an unrecognized `sk-proj-` key upstream under
    ENABLE_OPEN_BYOK_PASSTHROUGH because the caller pays for it. Nothing is
    forwarded here and rehydrate emits plaintext PII, so a prefix match must not
    grant vault access.
    """
    monkeypatch.setattr(settings, "OVERRIDE_CLIENT_AUTH", False)
    monkeypatch.setattr(settings, "ENABLE_OPEN_BYOK_PASSTHROUGH", True, raising=False)
    response = client.post(
        "/v1/guard/rehydrate",
        headers={"Authorization": "Bearer sk-proj-not-a-real-key", "X-Session-ID": "s"},
        json={"texts": ["anything"]},
    )
    assert response.status_code == 401


# --- Round trip -------------------------------------------------------------------


def test_redact_removes_the_raw_value(open_auth):
    masked = _redact(PROMPT)
    assert SECRET_EMAIL not in masked


def test_rehydrate_restores_the_raw_value(open_auth):
    masked = _redact(PROMPT)
    response = client.post("/v1/guard/rehydrate", headers=SESSION, json={"texts": [masked]})
    assert response.status_code == 200
    assert response.json()["texts"][0] == PROMPT


def test_rehydrate_is_scoped_to_the_session(open_auth):
    """A different session must not be able to resolve another session's tokens."""
    masked = _redact(PROMPT)
    other = {"X-Session-ID": "a-different-session", "Authorization": "Bearer dev-key"}
    response = client.post("/v1/guard/rehydrate", headers=other, json={"texts": [masked]})
    assert response.status_code == 200
    assert SECRET_EMAIL not in response.json()["texts"][0]


# --- Streaming --------------------------------------------------------------------


def _drive_stream(chunks: list, headers: dict = SESSION) -> list:
    """Feeds chunks through the stateless carry-over protocol, returning emissions."""
    carry = ""
    emissions = []
    for index, chunk in enumerate(chunks):
        response = client.post(
            "/v1/guard/rehydrate/stream",
            headers=headers,
            json={"text": chunk, "carry": carry, "final": index == len(chunks) - 1},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        emissions.append(body["text"])
        carry = body["carry"]
    assert carry == "", "final delta must flush the carry-over"
    return emissions


def test_placeholder_split_across_deltas_is_not_leaked(open_auth):
    """The differentiator: hold back a straddling placeholder, emit the rest now."""
    masked = _redact(PROMPT)
    split = masked.index("@")
    head, tail = masked[:split], masked[split:]

    emissions = _drive_stream([head, tail])

    # No emission may contain a fragment of the placeholder.
    for emission in emissions:
        assert "@" not in emission or SECRET_EMAIL in emission

    # Restored exactly, and identical to the non-streaming path.
    assert "".join(emissions) == PROMPT

    # Incremental: the leading plain text is released on the first delta rather
    # than withheld until end-of-stream.
    assert emissions[0] == "Email "


def test_stream_matches_non_streaming_result(open_auth):
    masked = _redact(PROMPT)
    batch = client.post("/v1/guard/rehydrate", headers=SESSION, json={"texts": [masked]})
    per_character = _drive_stream(list(masked))
    assert "".join(per_character) == batch.json()["texts"][0]


def test_oversized_carry_is_rejected(open_auth):
    response = client.post(
        "/v1/guard/rehydrate/stream",
        headers=SESSION,
        json={"text": "x", "carry": "c" * (64 * 1024 + 1)},
    )
    assert response.status_code == 413


# --- Input bounds -----------------------------------------------------------------


def test_texts_must_be_an_array(open_auth):
    response = client.post("/v1/guard/redact", headers=SESSION, json={"texts": "not-a-list"})
    assert response.status_code == 400


def test_texts_must_contain_only_strings(open_auth):
    response = client.post("/v1/guard/redact", headers=SESSION, json={"texts": [1, 2]})
    assert response.status_code == 400


def test_batch_size_is_bounded(open_auth):
    response = client.post("/v1/guard/redact", headers=SESSION, json={"texts": ["x"] * 257})
    assert response.status_code == 413


def test_total_characters_are_bounded(open_auth):
    response = client.post(
        "/v1/guard/redact", headers=SESSION, json={"texts": ["x" * 500_001] * 2}
    )
    assert response.status_code == 413


def test_invalid_json_is_rejected(open_auth):
    response = client.post(
        "/v1/guard/redact",
        headers={**SESSION, "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert response.status_code == 400
