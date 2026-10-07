"""The two interim wirings against the LiteLLM release that is actually installed.

The other files in this directory stub LiteLLM, so they prove our side of the contract and
nothing about LiteLLM's. On 2026-10-06 LiteLLM had changed `stream_holdback_chars` from a
number to a per-choice list and the shim's number was silently dropped: the stream went out
with no holdback and ended mid-stand-in. No test here could have seen it.

These tests run only when `litellm` is importable. The `litellm-contract` workflow installs
the newest release from PyPI (unpinned on purpose) and runs this file on a schedule and on
every change under `examples/integrations/litellm/`, so an upstream change to the request
LiteLLM sends, the response fields it reads, the hook names it calls or the stream types it
hands over fails here before a user finds it.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

litellm = pytest.importorskip("litellm")

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "integrations" / "litellm"
PLACEHOLDER = "<EMAIL_ADDRESS>"
PLAIN = "jane.doe@example.com"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EXAMPLE_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- generic guardrail API shim: LiteLLM's request in, LiteLLM's response model out ---------


@pytest.fixture
def shim(monkeypatch):
    monkeypatch.setenv("LITELLM_GUARDRAIL_SHIM_KEY", "shim-key")
    monkeypatch.setenv("SHIM_STREAMING_REHYDRATION", "true")
    return _load("litellm_guardrail_shim_live", "litellm_guardrail_shim.py")


def test_litellm_request_model_parses_into_the_shims_request(shim):
    """The fields the shim reads are the ones LiteLLM's own request model carries."""
    from litellm.types.proxy.guardrails.guardrail_hooks.generic_guardrail_api import GenericGuardrailAPIRequest

    sent = GenericGuardrailAPIRequest(
        texts=["Contact <EMAIL_ADDRESS>"], input_type="response", litellm_call_id="call-1", request_data={}
    )
    parsed = shim.GuardrailRequest.model_validate(sent.model_dump(mode="json"))
    assert parsed.texts == ["Contact <EMAIL_ADDRESS>"]
    assert parsed.input_type == "response"
    assert parsed.litellm_call_id == "call-1"


def test_litellm_reads_the_shims_streaming_holdback_as_a_per_choice_list(shim, monkeypatch):
    """`GenericGuardrailAPIResponse.from_dict` is what LiteLLM applies to the shim's reply. The
    holdback must survive it as one int per text; a dropped or reshaped holdback is the 10-06
    truncation again."""
    from fastapi.testclient import TestClient
    from litellm.types.proxy.guardrails.guardrail_hooks.generic_guardrail_api import GenericGuardrailAPIResponse

    async def fake(path, session_id, payload):
        if path.endswith("/stream"):
            return {"text": "Contact jane", "carry": ".doe@example.com"}
        return {"texts": ["Contact jane.doe@example.com"]}

    monkeypatch.setattr(shim, "_call_shield", fake)
    body = (
        TestClient(shim.app)
        .post(
            "/beta/litellm_basic_guardrail_api",
            json={"texts": ["Contact <EMAIL_ADDRESS>"], "input_type": "response", "litellm_call_id": "call-1"},
            headers={"x-api-key": "shim-key"},
        )
        .json()
    )
    read_back = GenericGuardrailAPIResponse.from_dict(body)
    assert read_back.action == "GUARDRAIL_INTERVENED"
    assert read_back.texts == ["Contact jane.doe@example.com"]
    assert read_back.stream_holdback_chars == [len(".doe@example.com")]


# --- in-process example: real base class, real hook names, real stream types ---------------


@pytest.fixture
def adapter(monkeypatch):
    module = _load("litellm_guardrail_live", "litellm_guardrail.py")
    guardrail = module.LLMShieldProxyGuardrail(guardrail_name=module.GUARDRAIL_NAME, api_base="http://shield.invalid")
    calls = []

    async def fake_call_shield(path, session_id, payload):
        calls.append(path)
        if path == module._REDACT_PATH:
            return {"texts": [t.replace(PLAIN, PLACEHOLDER) for t in payload["texts"]]}
        if path == module._REHYDRATE_PATH:
            return {"texts": [t.replace(PLACEHOLDER, PLAIN) for t in payload["texts"]]}
        text, carry, final = payload["text"], payload["carry"], payload["final"]
        combined = carry.replace(PLACEHOLDER, PLAIN) + text
        if final:
            return {"text": combined.replace(PLACEHOLDER, PLAIN), "carry": ""}
        opening = combined.rfind("<")
        if opening == -1:
            return {"text": combined, "carry": ""}
        return {"text": combined[:opening].replace(PLACEHOLDER, PLAIN), "carry": combined[opening:]}

    monkeypatch.setattr(guardrail, "_call_shield", fake_call_shield)
    return module, guardrail, calls


def test_the_example_subclasses_litellms_real_base_and_overrides_hooks_that_exist(adapter):
    from litellm.integrations.custom_guardrail import CustomGuardrail

    module, guardrail, _ = adapter
    assert isinstance(guardrail, CustomGuardrail)
    for hook in ("async_pre_call_hook", "async_post_call_success_hook", "async_post_call_streaming_iterator_hook", "apply_guardrail"):
        assert hasattr(CustomGuardrail, hook), f"LiteLLM no longer defines {hook}"
        ours = inspect.signature(getattr(module.LLMShieldProxyGuardrail, hook)).parameters
        theirs = inspect.signature(getattr(CustomGuardrail, hook)).parameters
        missing = [name for name in theirs if name not in ours and name not in ("args", "kwargs")]
        assert not missing, f"{hook}: LiteLLM now passes {missing}, the example does not accept them"


async def _stream(guardrail, chunks):
    async def source():
        for chunk in chunks:
            yield chunk

    data = {"litellm_metadata": {"llm_shield_session_id": f"{guardrail._session_id({})}"}}
    return [c async for c in guardrail.async_post_call_streaming_iterator_hook(None, source(), data)]


@pytest.mark.asyncio
async def test_a_real_chat_stream_is_restored(adapter):
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    _, guardrail, _ = adapter
    chunks = [
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content="Sending to <EMAIL_"))]),
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content="ADDRESS> now"))]),
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="stop")]),
    ]
    emitted = await _stream(guardrail, chunks)
    text = "".join(c.choices[0].delta.content or "" for c in emitted)
    assert text == f"Sending to {PLAIN} now"


@pytest.mark.asyncio
async def test_a_real_completions_stream_is_restored(adapter):
    """LiteLLM's Completions chunks are `TextChoices` with `text` and no `delta`; the finishing
    one carries `text=None`. Both facts are what the example relies on."""
    from litellm.types.utils import TextChoices, TextCompletionResponse

    _, guardrail, _ = adapter
    chunks = [
        TextCompletionResponse(choices=[TextChoices(text="Echo: <EMAIL_", index=0)]),
        TextCompletionResponse(choices=[TextChoices(text="ADDRESS> please", index=0)]),
        TextCompletionResponse(choices=[TextChoices(text=None, index=0, finish_reason="stop")]),
    ]
    assert all(not hasattr(c.choices[0], "delta") for c in chunks), "TextChoices grew a delta field"
    emitted = await _stream(guardrail, chunks)
    text = "".join(c.choices[0].text or "" for c in emitted)
    assert text == f"Echo: {PLAIN} please"


@pytest.mark.asyncio
async def test_a_real_non_streaming_reply_is_restored(adapter):
    from litellm.types.utils import Choices, Message, ModelResponse

    _, guardrail, _ = adapter
    response = ModelResponse(choices=[Choices(index=0, message=Message(content="Sending to <EMAIL_ADDRESS>"))])
    data = {"litellm_metadata": {"llm_shield_session_id": "x"}}
    restored = await guardrail.async_post_call_success_hook(data, None, response)
    assert restored.choices[0].message.content == f"Sending to {PLAIN}"
