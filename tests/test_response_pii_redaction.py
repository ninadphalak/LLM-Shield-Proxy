"""Response-side redaction: the half of the stream the proxy used to forward untouched.

Until 1.6.0 the response path rehydrated this session's own placeholders and forwarded
everything else. That is correct for the caller's data and wrong for the model's: a value
the model produced was never in the vault, so nothing redacted it and it reached the
client. Measured at LeakRate 1.00 by the v2 conformance profile.

The difficulty is that the two required behaviours are OPPOSITE and operate on the same
bytes. In SYNTHETIC masking mode a vault token is a realistic-looking value, so a detector
cannot distinguish "the surrogate we substituted" from "PII the model invented". Order and
vault-awareness are what make it work, and every test here is about one of those two.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer

USER_EMAIL = "lylyfwzv@example.com"
MODEL_EMAIL = "nuwpcbba@example.com"
MODEL_SSN = "219-09-9999"


@pytest.fixture
def response_redaction_on():
    previous = settings.ENABLE_RESPONSE_PII_REDACTION
    settings.ENABLE_RESPONSE_PII_REDACTION = True
    try:
        yield
    finally:
        settings.ENABLE_RESPONSE_PII_REDACTION = previous


@pytest.fixture
def response_redaction_off():
    previous = settings.ENABLE_RESPONSE_PII_REDACTION
    settings.ENABLE_RESPONSE_PII_REDACTION = False
    try:
        yield
    finally:
        settings.ENABLE_RESPONSE_PII_REDACTION = previous


def _drive(chunks: list[str], vault: Vault) -> str:
    buffer = SSERehydrationBuffer(vault)
    out = "".join(buffer.process_delta_text(c) for c in chunks)
    return out + buffer.process_delta_text("", is_final=True)


def _vault_with_user_email() -> tuple[Vault, str]:
    vault = Vault()
    return vault, vault.get_or_create_token(USER_EMAIL, "EMAIL")


def test_both_halves_at_once(response_redaction_on):
    """The property that no measured gateway had: restore ours, redact theirs, one stream."""
    vault, token = _vault_with_user_email()
    seen = _drive([f"You sent: {token} and Reference: {MODEL_EMAIL} end"], vault)

    assert USER_EMAIL in seen, "the caller's own value was not restored"
    assert MODEL_EMAIL not in seen, "a model-originated value reached the client"


def test_model_value_split_across_chunks_is_still_caught(response_redaction_on):
    """The chunk-boundary case. A fixed tail is not enough; the cut must not fall inside
    a value, which is what `_response_retention_length` is for."""
    vault, token = _vault_with_user_email()
    seen = _drive(["You sent: ", token, " and Reference: nuwpc", "bba@example.com", " end"], vault)

    assert USER_EMAIL in seen
    assert MODEL_EMAIL not in seen, "the value leaked because it straddled a chunk boundary"


@pytest.mark.parametrize("split", range(1, len(MODEL_SSN)))
def test_every_split_point_of_a_model_value_is_caught(split, response_redaction_on):
    """Bounded-exhaustive over split points, not one convenient split.

    One chosen boundary proves nothing: the defect is that SOME boundary leaks.
    """
    vault, token = _vault_with_user_email()
    seen = _drive(
        [f"You sent: {token}. Ref ", MODEL_SSN[:split], MODEL_SSN[split:], " done"], vault
    )

    assert MODEL_SSN not in seen, f"leaked when split after {split} characters"
    assert USER_EMAIL in seen, f"fidelity lost when split after {split} characters"


def test_value_at_the_very_end_of_the_stream_is_caught(response_redaction_on):
    """The retained tail is never scanned until the stream ends, so a value placed last
    is exactly where an attacker would put it."""
    vault, token = _vault_with_user_email()
    seen = _drive([f"You sent: {token}. Ref {MODEL_EMAIL}"], vault)

    assert MODEL_EMAIL not in seen
    assert USER_EMAIL in seen


def test_the_callers_own_value_survives_a_detector_that_would_match_it(response_redaction_on):
    """In SYNTHETIC mode the token IS a plausible email, so the detector will match it.
    Skipping known tokens is the only thing that stops the caller's data being destroyed.
    """
    vault, token = _vault_with_user_email()
    from llm_shield_proxy.engines.pii_engine import pii_engine

    assert pii_engine.detect_spans(token), (
        "precondition: the surrogate must look like PII to the detector, or this test "
        "proves nothing"
    )
    seen = _drive([f"Here is {token}."], vault)
    assert USER_EMAIL in seen
    assert "REDACTED" not in seen


def test_off_by_default_forwards_model_values(response_redaction_off):
    """The documented default. Stated as a test so a silent flip is a failing build."""
    vault, token = _vault_with_user_email()
    seen = _drive([f"You sent: {token} and Reference: {MODEL_EMAIL} end"], vault)

    assert USER_EMAIL in seen
    assert MODEL_EMAIL in seen, "default changed: this is a behaviour change for callers"


def test_default_setting_is_off():
    """Read the declared default from the Settings model, not from the live proxy object
    that other tests mutate."""
    from llm_shield_proxy.core.config import Settings

    assert Settings.model_fields["ENABLE_RESPONSE_PII_REDACTION"].default is False
    assert Settings.model_fields["RESPONSE_PII_SCAN_WINDOW"].default == 64


def test_retention_is_bounded(response_redaction_on):
    """An unbounded hold-back would remove the leak by removing streaming, which is the
    trade the LiteLLM row makes. The buffer must stay bounded by the configured window."""
    vault, _token = _vault_with_user_email()
    buffer = SSERehydrationBuffer(vault)
    for _ in range(200):
        buffer.process_delta_text("harmless words with spaces ")
        assert len(buffer.content_buffer) <= settings.RESPONSE_PII_SCAN_WINDOW + 64, (
            "buffer grew past the retention window; this is whole-response buffering"
        )


def test_output_is_emitted_incrementally(response_redaction_on):
    """Streaming must survive the feature. A gateway that emits nothing until the end has
    traded the leak for the property streaming exists to provide."""
    vault, _token = _vault_with_user_email()
    buffer = SSERehydrationBuffer(vault)
    emitted = [buffer.process_delta_text("a sentence of harmless words ") for _ in range(10)]

    assert any(piece for piece in emitted), "nothing was emitted before the final flush"


def test_scan_failure_forwards_rather_than_blanking(response_redaction_on, monkeypatch):
    """Deliberately not fail-closed. Failing closed here breaks the response for a
    scanner error rather than for a leak; the gap is logged instead of hidden."""
    vault, _token = _vault_with_user_email()
    from llm_shield_proxy.engines import pii_engine as engine_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(engine_module.pii_engine, "detect_spans", boom)
    seen = _drive(["some ordinary text here"], vault)
    assert "some ordinary text here" in seen
