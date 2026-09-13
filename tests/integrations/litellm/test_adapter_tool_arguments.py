"""The adapter has to restore tool arguments, not only message text.

`_collect_tool_arguments` and `_collect_responses_fields` redact a tool call's
``arguments`` on the way out. Nothing walked those same fields on the way back, so a
placeholder reached the application *inside a tool call* -- ``send_email`` invoked with
``<EMAIL_ADDRESS>`` as the recipient -- with no error raised anywhere along the path.
The same asymmetry held for an Anthropic ``tool_use`` block's ``input`` and for the
Responses API's ``function_call.arguments``.

`test_adapter.py` reads the adapter rather than running it, which is why it could not
see this. These tests execute the response walk instead, driving the hooks with the
transport stubbed.

LiteLLM is not installed in this suite, and `test_adapter.py` exists to keep it that
way, so the host is stood in for: only the names the adapter imports at module scope are
provided, and nothing from the host executes. The stub keeps one property of the real
Shield and no other -- a token is withheld from its opening bracket until the stream is
final, and the withheld region is returned restored -- because that is the property the
rest of this file depends on.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / "examples" / "integrations" / "litellm" / "litellm_guardrail.py"

PLACEHOLDER = "<EMAIL_ADDRESS>"
PLAIN = "jane.doe@example.com"
SESSION_ID = "litellm-adapter-tool-arguments"


def _restore(text: str) -> str:
    """The Shield's substitution, as a plain string replace."""
    return text.replace(PLACEHOLDER, PLAIN)


def _step(text: str, carry: str, final: bool) -> tuple[str, str]:
    """Stands in for ``/v1/guard/rehydrate/stream``.

    The contract is the one vendored in ``litellm_guardrail_shim.py``: the endpoint
    answers the text that is safe to emit now plus the text it is still withholding, and
    the two together are the restored text. The policy here is the single property the
    adapter must survive -- everything from the last token opening is held back until the
    call is final, so a token arriving in fragments is never emitted in fragments -- and
    the withheld region comes back restored. An incomplete token restores to itself,
    which is what lets it rejoin the rest of the token on the next call.
    """
    combined = _restore(carry) + text
    if final:
        return _restore(combined), ""
    opening = combined.rfind("<")
    if opening == -1:
        return combined, ""
    return _restore(combined[:opening]), _restore(combined[opening:])


def _module(name: str, **attributes: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def _field(holder: object, name: str) -> object:
    """Reads a field from a dict or an object, the way the adapter does."""
    if isinstance(holder, dict):
        return holder.get(name)
    return getattr(holder, name, None)



def _stub_host(monkeypatch) -> None:
    """Registers the host names the adapter imports at module scope.

    ``CustomGuardrail`` is a bare base and ``log_guardrail_information`` is the identity
    function: this file is about the adapter's own walk, and a real base class would drag
    the host's behaviour into the assertions.
    """

    class _CustomGuardrail:
        def __init__(self, guardrail_name=None, **_kwargs):
            self.guardrail_name = guardrail_name

        def should_run_guardrail(self, data=None, event_type=None):
            return True

    class _GuardrailRaisedException(Exception):
        def __init__(self, guardrail_name=None, message=None):
            super().__init__(message)
            self.guardrail_name = guardrail_name

    def _guardrail_information(func=None, **_kwargs):
        return (lambda inner: inner) if func is None else func

    logger = types.SimpleNamespace(
        debug=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )

    stubs = (
        _module("litellm"),
        _module("litellm._logging", verbose_proxy_logger=logger),
        _module("litellm.exceptions", GuardrailRaisedException=_GuardrailRaisedException),
        _module("litellm.integrations"),
        _module(
            "litellm.integrations.custom_guardrail",
            CustomGuardrail=_CustomGuardrail,
            log_guardrail_information=_guardrail_information,
        ),
        _module("litellm.llms"),
        _module("litellm.llms.custom_httpx"),
        _module(
            "litellm.llms.custom_httpx.http_handler",
            get_async_httpx_client=lambda **_kwargs: object(),
            httpxSpecialProvider=types.SimpleNamespace(GuardrailCallback="guardrail"),
        ),
        _module("litellm.proxy"),
        _module("litellm.proxy._types", UserAPIKeyAuth=object),
        _module("litellm.types"),
        _module(
            "litellm.types.guardrails",
            GuardrailEventHooks=types.SimpleNamespace(pre_call="pre_call", post_call="post_call"),
        ),
        _module("litellm.types.utils", GenericGuardrailAPIInputs=dict),
    )
    for stub in stubs:
        monkeypatch.setitem(sys.modules, stub.__name__, stub)


def _load_adapter(monkeypatch):
    _stub_host(monkeypatch)
    name = "litellm_guardrail_under_test"
    spec = importlib.util.spec_from_file_location(name, ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def harness(monkeypatch):
    """The adapter with its Shield transport replaced by ``_step``/``_restore``.

    ``_call_shield`` is stubbed rather than ``_rehydrate`` on purpose: that keeps the real
    length check and the real positional write-back inside the path under test, which is
    where an off-by-one in the new batching would hide. Every call is recorded, so a test
    can assert what came back *and* that it took a single round trip.
    """
    module = _load_adapter(monkeypatch)
    guardrail = module.LLMShieldProxyGuardrail(
        guardrail_name=module.GUARDRAIL_NAME,
        api_base="http://shield.invalid",
    )
    calls: list[tuple[str, str, dict]] = []

    async def fake_call_shield(path, session_id, payload):
        calls.append((path, session_id, payload))
        if path == module._REDACT_PATH:
            return {"texts": [text.replace(PLAIN, PLACEHOLDER) for text in payload["texts"]]}
        if path == module._REHYDRATE_PATH:
            return {"texts": [_restore(text) for text in payload["texts"]]}
        if path == module._REHYDRATE_STREAM_PATH:
            emitted, carry = _step(payload["text"], payload["carry"], payload["final"])
            return {"text": emitted, "carry": carry}
        raise AssertionError(f"unexpected Shield path {path}")

    monkeypatch.setattr(guardrail, "_call_shield", fake_call_shield)
    return module, guardrail, calls


# --- fakes and drivers ------------------------------------------------------


def _tool_call(arguments: str, index: int = 0, as_object: bool = False):
    """A tool call as LiteLLM delivers it: a model object, or a dict.

    Streaming deltas always carry an index -- it is what a client concatenates fragments
    by -- so the fake carries one in both shapes.
    """
    if as_object:
        return types.SimpleNamespace(index=index, function=types.SimpleNamespace(name="send_email", arguments=arguments))
    return {"index": index, "function": {"name": "send_email", "arguments": arguments}}


def _request_data(module) -> dict:
    return {"metadata": {module._SESSION_METADATA_KEY: SESSION_ID}}


async def _non_streaming(module, guardrail, response):
    return await guardrail.async_post_call_success_hook(_request_data(module), None, response)


async def _stream(module, guardrail, chunks):
    async def source():
        for chunk in chunks:
            yield chunk

    hook = guardrail.async_post_call_streaming_iterator_hook
    return [chunk async for chunk in hook(None, source(), _request_data(module))]


def _batch(calls) -> list[str]:
    """The exact texts sent in the one rehydrate batch."""
    rehydrates = [call for call in calls if call[0] == "/v1/guard/rehydrate"]
    assert len(rehydrates) == 1, f"expected a single rehydrate round trip, got {len(rehydrates)}"
    return rehydrates[0][2]["texts"]


# --- non-streaming: OpenAI chat completions ---------------------------------


@pytest.mark.asyncio
async def test_openai_tool_calls_and_legacy_function_call_are_restored(harness):
    """The values a tool is invoked with have to come back, not the stand-ins.

    `_collect_tool_arguments` redacts `arguments` before the provider ever sees it, so the
    model only emitted `<EMAIL_ADDRESS>`. Leaving the reply unrestored hands the
    application a placeholder to act on, and every hop reports success -- which is what
    let this survive review.
    """
    module, guardrail, calls = harness
    message = types.SimpleNamespace(
        content="Sending to <EMAIL_ADDRESS>",
        tool_calls=[_tool_call('{"to": "<EMAIL_ADDRESS>"}', as_object=True)],
        function_call=types.SimpleNamespace(name="legacy_send", arguments='{"to": "<EMAIL_ADDRESS>"}'),
    )
    response = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    assert await _non_streaming(module, guardrail, response) is response

    assert message.content == f"Sending to {PLAIN}"
    assert message.tool_calls[0].function.arguments == f'{{"to": "{PLAIN}"}}'
    assert message.function_call.arguments == f'{{"to": "{PLAIN}"}}'
    # One round trip, in document order: the shield maps its answers back by position.
    assert _batch(calls) == [
        "Sending to <EMAIL_ADDRESS>",
        '{"to": "<EMAIL_ADDRESS>"}',
        '{"to": "<EMAIL_ADDRESS>"}',
    ]


@pytest.mark.asyncio
async def test_anthropic_tool_use_input_leaves_are_restored(harness):
    """`tool_use.input` is a JSON object rather than a string, so it is walked leaf by leaf.

    The request side redacts those leaves, so a reply that skips the block leaves the
    model's tool call holding placeholders. The non-string leaves are asserted too: an
    `attempts` that came back as a string would break the same call a different way.
    """
    module, guardrail, calls = harness
    response = {
        "type": "message",
        "content": [
            {"type": "text", "text": "Sending to <EMAIL_ADDRESS>"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "send_email",
                "input": {"to": "<EMAIL_ADDRESS>", "cc": ["<EMAIL_ADDRESS>"], "attempts": 3, "dryrun": False},
            },
        ],
    }

    assert await _non_streaming(module, guardrail, response) is response

    assert response["content"][0]["text"] == f"Sending to {PLAIN}"
    tool_input = response["content"][1]["input"]
    assert tool_input["to"] == PLAIN
    assert tool_input["cc"] == [PLAIN]
    assert tool_input["attempts"] == 3
    assert tool_input["dryrun"] is False
    assert _batch(calls) == ["Sending to <EMAIL_ADDRESS>", "<EMAIL_ADDRESS>", "<EMAIL_ADDRESS>"]


@pytest.mark.asyncio
async def test_responses_function_call_arguments_and_output_are_restored(harness):
    """`arguments` and `output` sit on the item, not inside `content`, so they need a walk
    of their own -- the one `_collect_responses_fields` already does on the request side.

    One item is a dict and one is an object, because a reply arrives in either shape
    depending on how far it has been deserialised.
    """
    module, guardrail, calls = harness
    response = types.SimpleNamespace(
        output=[
            {"type": "function_call", "call_id": "call_1", "arguments": '{"to": "<EMAIL_ADDRESS>"}'},
            types.SimpleNamespace(type="function_call_output", call_id="call_1", output='{"to": "<EMAIL_ADDRESS>"}'),
            types.SimpleNamespace(
                type="message", content=[types.SimpleNamespace(type="output_text", text="Done, <EMAIL_ADDRESS>")]
            ),
        ]
    )

    assert await _non_streaming(module, guardrail, response) is response

    assert response.output[0]["arguments"] == f'{{"to": "{PLAIN}"}}'
    assert response.output[1].output == f'{{"to": "{PLAIN}"}}'
    assert response.output[2].content[0].text == f"Done, {PLAIN}"
    assert _batch(calls) == ['{"to": "<EMAIL_ADDRESS>"}', '{"to": "<EMAIL_ADDRESS>"}', "Done, <EMAIL_ADDRESS>"]


@pytest.mark.asyncio
async def test_apply_guardrail_restores_response_tool_calls(harness):
    """LiteLLM populates `tool_calls` on this path and reads it back, so the UI's Test
    guardrail button and every translation handler need them restored here as well.

    The caller's own list is left untouched: this method hands back a new mapping.
    """
    module, guardrail, calls = harness
    arguments = '{"to": "<EMAIL_ADDRESS>"}'
    inputs = {
        "texts": ["Done, <EMAIL_ADDRESS>"],
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "send_email", "arguments": arguments}}],
    }

    merged = await guardrail.apply_guardrail(inputs, _request_data(module), "response")

    assert merged["texts"] == [f"Done, {PLAIN}"]
    assert merged["tool_calls"][0]["function"]["arguments"] == f'{{"to": "{PLAIN}"}}'
    assert inputs["tool_calls"][0]["function"]["arguments"] == arguments
    assert _batch(calls) == ["Done, <EMAIL_ADDRESS>", arguments]


@pytest.mark.asyncio
async def test_apply_guardrail_leaves_request_tool_calls_to_the_native_hook(harness):
    """Redacting here as well would redact the same arguments twice, against a second
    vault, so the request direction keeps to `texts`.
    """
    module, guardrail, calls = harness
    inputs = {"texts": [PLAIN], "tool_calls": [{"function": {"arguments": '{"to": "' + PLAIN + '"}'}}]}

    merged = await guardrail.apply_guardrail(inputs, _request_data(module), "request")

    assert merged["tool_calls"] == inputs["tool_calls"]
    assert calls[0][0] == module._REDACT_PATH
    assert calls[0][2]["texts"] == [PLAIN]


# --- streaming --------------------------------------------------------------


class _Delta:
    """A streamed delta. The adapter reads `content` and `tool_calls` and nothing else."""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta=None, finish_reason=None, index=0):
        self.delta = delta if delta is not None else _Delta()
        self.finish_reason = finish_reason
        self.index = index


class _Chunk:
    """A chunk, plus the one host method the adapter calls on it: `model_copy`.

    `_flush_trailing` copies the last chunk to build a chunk of its own, so the fake has
    to copy the way the host does or that path is unreachable here.
    """

    def __init__(self, choices):
        self.choices = choices

    def model_copy(self, deep: bool = False):
        return _Chunk(copy.deepcopy(self.choices))


def _arguments_by_tool_call(chunks) -> dict:
    """What a client would concatenate, keyed by tool-call index."""
    accumulated: dict = {}
    for chunk in chunks:
        for choice in chunk.choices:
            for call in getattr(choice.delta, "tool_calls", None) or ():
                index = _field(call, "index")
                fragment = _field(_field(call, "function"), "arguments") or ""
                accumulated[index] = accumulated.get(index, "") + fragment
    return accumulated


def _content_by_choice(chunks) -> dict:
    """What a client would concatenate, keyed by choice index."""
    accumulated: dict = {}
    for chunk in chunks:
        for choice in chunk.choices:
            fragment = getattr(choice.delta, "content", None)
            if fragment:
                accumulated[choice.index] = accumulated.get(choice.index, "") + fragment
    return accumulated


def _finishing_chunk(chunks):
    return next(chunk for chunk in chunks for choice in chunk.choices if choice.finish_reason)


@pytest.mark.asyncio
async def test_streamed_tool_arguments_split_across_chunks_are_restored(harness):
    """A token split across two argument fragments must never be emitted in fragments.

    Arguments arrive as fragments the client concatenates by index, so the withheld tail
    has to rejoin the tool call it came from: `{"to": "<EMAIL_` then `ADDRESS>"}`.
    """
    module, guardrail, _ = harness
    chunks = [
        _Chunk([_Choice(_Delta(tool_calls=[_tool_call('{"to": "<EMAIL_')]))]),
        _Chunk([_Choice(_Delta(tool_calls=[_tool_call('ADDRESS>"}')]))]),
        _Chunk([_Choice(_Delta(), finish_reason="tool_calls")]),
    ]

    emitted = await _stream(module, guardrail, chunks)

    assert _arguments_by_tool_call(emitted) == {0: f'{{"to": "{PLAIN}"}}'}


@pytest.mark.asyncio
async def test_the_flush_lands_in_the_finish_chunk_and_not_after_it(harness):
    """A client parses a tool call's arguments when it sees the finish_reason.

    A tail delivered in a chunk after that one is JSON the client has already given up on
    parsing, so the tail has to ride the chunk that ends the choice -- as an index-only
    continuation entry, which is the shape a client concatenates it by.
    """
    module, guardrail, _ = harness
    chunks = [
        _Chunk([_Choice(_Delta(tool_calls=[_tool_call('{"to": "<EMAIL_ADDRESS>"}')]))]),
        _Chunk([_Choice(_Delta(), finish_reason="tool_calls")]),
    ]

    emitted = await _stream(module, guardrail, chunks)

    finishing = _finishing_chunk(emitted)
    assert emitted[-1] is finishing, "nothing may follow the chunk that ends the choice"
    withheld_tail = PLAIN + '"}'  # the restored tail, including the JSON document's close
    continuations = finishing.choices[0].delta.tool_calls
    assert continuations == [{"index": 0, "function": {"arguments": withheld_tail}}]
    assert "id" not in continuations[0], "a continuation entry carries no id"
    assert "name" not in continuations[0]["function"], "a continuation entry carries no name"
    assert _arguments_by_tool_call(emitted) == {0: f'{{"to": "{PLAIN}"}}'}


@pytest.mark.asyncio
async def test_two_tool_calls_in_one_stream_do_not_share_a_window(harness):
    """Each tool call is its own token stream, keyed by (choice, tool-call index).

    One shared window splices the tail withheld for the first tool call onto the second,
    corrupting both arguments documents -- and leaking a half-printed token into one the
    client then parses. That is why each value is asserted on its own.
    """
    module, guardrail, _ = harness
    chunks = [
        _Chunk(
            [
                _Choice(
                    _Delta(
                        tool_calls=[
                            _tool_call('{"to": "<EMAIL_ADDRESS>"}', index=0),
                            _tool_call('{"cc": "<EMAIL_ADDRESS>"}', index=1),
                        ]
                    )
                )
            ]
        ),
        _Chunk([_Choice(_Delta(), finish_reason="tool_calls")]),
    ]

    emitted = await _stream(module, guardrail, chunks)

    assert _arguments_by_tool_call(emitted) == {0: f'{{"to": "{PLAIN}"}}', 1: f'{{"cc": "{PLAIN}"}}'}


@pytest.mark.asyncio
async def test_streamed_tool_calls_are_read_in_their_object_shape(harness):
    """A real stream hands the delta's tool calls over as objects, not dicts.

    Every other response walk in the adapter reads both shapes; this is what keeps the
    streaming one from being the exception.
    """
    module, guardrail, _ = harness
    chunks = [
        _Chunk([_Choice(_Delta(tool_calls=[_tool_call('{"to": "<EMAIL_ADDRESS>"}', as_object=True)]))]),
        _Chunk([_Choice(_Delta(), finish_reason="tool_calls")]),
    ]

    emitted = await _stream(module, guardrail, chunks)

    assert _arguments_by_tool_call(emitted) == {0: f'{{"to": "{PLAIN}"}}'}


@pytest.mark.asyncio
async def test_a_stream_without_a_finish_reason_still_gets_its_tail(harness):
    """The last-resort net: some streams simply end.

    Without it a reply that stopped mid-tool-call would drop the withheld tail and hand the
    application truncated arguments.
    """
    module, guardrail, _ = harness
    chunks = [_Chunk([_Choice(_Delta(tool_calls=[_tool_call('{"to": "<EMAIL_ADDRESS>"}')]))])]

    emitted = await _stream(module, guardrail, chunks)

    assert _arguments_by_tool_call(emitted) == {0: f'{{"to": "{PLAIN}"}}'}


@pytest.mark.asyncio
async def test_streamed_content_is_still_restored_per_choice(harness):
    """Content now shares the window map with tool calls, so it is asserted again.

    Its windows are keyed `(choice, None)`, and one choice's withheld tail must not be
    spliced onto the next choice -- the property the tool-call windows were added around.
    """
    module, guardrail, _ = harness
    chunks = [
        _Chunk(
            [
                _Choice(_Delta(content="Sending to <EMAIL_"), index=0),
                _Choice(_Delta(content="cc <EMAIL_"), index=1),
            ]
        ),
        _Chunk(
            [
                _Choice(_Delta(content="ADDRESS>"), index=0),
                _Choice(_Delta(content="ADDRESS>"), index=1),
            ]
        ),
        _Chunk(
            [
                _Choice(_Delta(), finish_reason="stop", index=0),
                _Choice(_Delta(), finish_reason="stop", index=1),
            ]
        ),
    ]

    emitted = await _stream(module, guardrail, chunks)

    assert _content_by_choice(emitted) == {0: f"Sending to {PLAIN}", 1: f"cc {PLAIN}"}
