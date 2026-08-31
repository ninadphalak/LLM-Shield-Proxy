"""Tier 3 ONNX NER, running real inference on a real model.

``tests/conftest.py`` sets ``ENABLE_TIER3_ONNX_NER = False`` for the whole
suite, and the pre-existing test named for Tier 3 exercised the regex ``PERSON``
fallback -- so nothing had ever loaded ``ONNX_MODEL_PATH``, a tokenizer and a
model through ``onnxruntime``.

CI now caches a pinned quantized BERT-family NER export and points
``SHIELD_TEST_ONNX_MODEL_DIR`` at it. The module skips when that is unset, and
``SHIELD_REQUIRE_ONNX=1`` (set by CI) turns the skip into a failure so the job
cannot pass without having run the model.

The load-bearing design decision here: every assertion is written so the regex
fallback **cannot** satisfy it. `_init_onnx_model` swallows load errors and the
inference block swallows runtime errors, both falling back to
``TIER3_NER_PATTERNS`` -- so a test that merely checks "a PERSON was found"
would pass with the model absent, broken, or silently erroring. The cases below
are ones where the two detectors disagree, in both directions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault

MODEL_DIR: Optional[str] = os.environ.get("SHIELD_TEST_ONNX_MODEL_DIR")
REQUIRE_ONNX = os.environ.get("SHIELD_REQUIRE_ONNX") == "1"


def _skip_reason() -> Optional[str]:
    if not MODEL_DIR:
        return "SHIELD_TEST_ONNX_MODEL_DIR is not set; no cached ONNX NER model available"
    model = Path(MODEL_DIR) / "model.onnx"
    tokenizer = Path(MODEL_DIR) / "tokenizer.json"
    if not model.exists():
        return f"{model} does not exist"
    if not tokenizer.exists():
        return f"{tokenizer} does not exist"
    for module in ("onnxruntime", "tokenizers", "numpy"):
        try:
            __import__(module)
        except ImportError:
            return f"the `ner` extra is not installed ({module} missing)"
    return None


_SKIP = _skip_reason()

if _SKIP and REQUIRE_ONNX:
    raise RuntimeError(
        f"SHIELD_REQUIRE_ONNX=1 but Tier 3 cannot run: {_SKIP}. "
        "Refusing to skip: this module exists to exercise real inference."
    )

pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")


@pytest.fixture(scope="module")
def onnx_engine():
    """A PIIEngine with the real model loaded, not the heuristic fallback."""
    from llm_shield_proxy.engines.pii_engine import PIIEngine

    previous = (settings.ENABLE_TIER3_ONNX_NER, settings.ONNX_MODEL_PATH)
    settings.ENABLE_TIER3_ONNX_NER = True
    settings.ONNX_MODEL_PATH = str(Path(MODEL_DIR) / "model.onnx")
    try:
        engine = PIIEngine()
        assert engine._onnx_session is not None, (
            "the ONNX session did not load; _init_onnx_model swallowed the error "
            "and this engine would silently be the regex fallback"
        )
        assert engine._tokenizer is not None, "tokenizer.json was not loaded next to the model"
        yield engine
    finally:
        settings.ENABLE_TIER3_ONNX_NER, settings.ONNX_MODEL_PATH = previous


@pytest.fixture(scope="module")
def fallback_engine():
    """The same engine with Tier 3 on but no model: the regex PERSON heuristic."""
    from llm_shield_proxy.engines.pii_engine import PIIEngine

    previous = (settings.ENABLE_TIER3_ONNX_NER, settings.ONNX_MODEL_PATH)
    settings.ENABLE_TIER3_ONNX_NER = False
    settings.ONNX_MODEL_PATH = None
    try:
        engine = PIIEngine()
        assert engine._onnx_session is None
        yield engine
    finally:
        settings.ENABLE_TIER3_ONNX_NER, settings.ONNX_MODEL_PATH = previous


def _entities(engine, text: str) -> list[tuple[str, str]]:
    return [(entity, value) for _, _, entity, value in engine.detect_spans(text)]


def test_model_metadata_matches_what_the_engine_feeds_it(onnx_engine):
    """The engine passes exactly two inputs; assert the pinned model takes two.

    ``detect_spans`` builds ``ort_inputs`` from ``get_inputs()[0]`` and
    ``get_inputs()[1]`` only. A BERT export that also requires
    ``token_type_ids`` raises inside ``session.run`` and is silently downgraded
    to the regex fallback -- which is why the model choice is a compatibility
    constraint, not a preference, and why it is asserted rather than assumed.
    """
    inputs = [i.name for i in onnx_engine._onnx_session.get_inputs()]
    assert inputs[:2] == ["input_ids", "attention_mask"]
    assert len(inputs) == 2, (
        f"the cached model requires {inputs}, but the engine only supplies the first two; "
        "inference would fail and fall back to regex"
    )


@pytest.mark.parametrize(
    "text,name",
    [
        ("The report was authored by Nakamura last quarter.", "Nakamura"),
        ("Please escalate this to Okonkwo immediately.", "Okonkwo"),
        ("Escalate to Ravi.", "Ravi"),
    ],
)
def test_single_token_names_are_detected_only_by_real_inference(
    onnx_engine, fallback_engine, text, name
):
    """The regex needs two consecutive capitalised words; the model does not.

    A single-token surname is therefore a clean discriminator: a pass here
    cannot come from `TIER3_NER_PATTERNS`.
    """
    assert _entities(fallback_engine, text) == [], (
        "the regex fallback matched this text, so it no longer discriminates"
    )
    assert ("PERSON", name) in _entities(onnx_engine, text)


def test_non_ascii_names_are_detected_by_the_model(onnx_engine, fallback_engine):
    """Accented and non-Latin-1 names the ASCII-anchored regex cannot express."""
    text = "Send the invoice to Björk Guðmundsdóttir before Friday."

    assert _entities(fallback_engine, text) == []
    detected = _entities(onnx_engine, text)
    assert any(entity == "PERSON" and "Guðmundsdóttir" in value for entity, value in detected), detected


def test_the_model_rejects_title_case_phrases_the_regex_flags(onnx_engine, fallback_engine):
    """Discrimination in the other direction: fewer false positives.

    `TIER3_NER_PATTERNS` treats any run of capitalised words as a PERSON, so it
    redacts ordinary document titles. Real inference does not.
    """
    text = "Deep Learning Conference registration is now open."

    assert ("PERSON", "Deep Learning Conference") in _entities(fallback_engine, text)
    assert _entities(onnx_engine, text) == []


def test_inference_runs_without_falling_back(onnx_engine, caplog):
    """Nothing may reach the `except` branch that quietly re-runs the regex."""
    caplog.set_level("DEBUG", logger="llm_shield_proxy.engines.pii_engine")

    detected = _entities(onnx_engine, "Forward the summary to Nakamura today.")

    assert ("PERSON", "Nakamura") in detected
    assert not any("ONNX inference failed" in record.getMessage() for record in caplog.records), (
        "inference raised and was downgraded to the regex fallback"
    )


def test_model_detected_entities_are_masked_and_rehydrate(onnx_engine):
    """A model-only detection travels the full mask/rehydrate round trip."""
    vault = Vault(synthetic=False)
    text = "Please escalate this to Okonkwo immediately."

    redacted = onnx_engine.redact_text(text, vault)

    assert "Okonkwo" not in redacted
    assert "[PERSON_1]" in redacted
    assert vault.rehydrate(redacted) == text


def test_tier1_still_runs_alongside_the_model(onnx_engine):
    """Enabling Tier 3 must not displace the structured detectors."""
    text = "Please email Nakamura at contact@example.com regarding card 4111111111111111."

    entities = _entities(onnx_engine, text)
    kinds = {entity for entity, _ in entities}

    assert {"EMAIL", "CREDIT_CARD"} <= kinds, entities
    # The PERSON here is model-only: the regex fallback needs two capitalised
    # words in a row, so its presence proves both cascades ran on one pass.
    assert ("PERSON", "Nakamura") in entities, entities


def test_the_engine_labels_every_model_entity_as_person(onnx_engine):
    """Documents the simplified label parsing, so the limitation is visible.

    `detect_spans` treats any non-``O`` prediction as ``PERSON`` -- it never
    reads the model's ``id2label``. The pinned model tags LOC and ORG too, so a
    city is reported as a PERSON. That is a real behaviour of the shipped code
    and the reason the feature's scope note stays narrow; it is asserted here so
    a future change to label handling is a deliberate, visible one.
    """
    entities = _entities(onnx_engine, "The Lisbon office confirmed the transfer.")

    assert entities, "the model found nothing, so this documents nothing"
    assert {entity for entity, _ in entities} == {"PERSON"}
    assert any(value == "Lisbon" for _, value in entities), entities
