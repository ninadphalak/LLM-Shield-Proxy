"""Unit tests for Server-Sent Events (SSE) streaming rehydration and buffer boundaries."""

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer, rehydrate_sse_stream


def test_sse_buffer_split_tag_handling():
    """Tests that placeholder tags split across SSE chunk boundaries are retained and flushed cleanly."""
    vault = Vault(synthetic=False)
    t_email = vault.get_or_create_token("sarah@skynet.com", "EMAIL")
    assert t_email == "[EMAIL_1]"

    buffer = SSERehydrationBuffer(vault)

    # Chunk 1 ends in a split tag "[EM"
    res1 = buffer.process_delta_text("Hello, contact info is [EM")
    assert res1 == "Hello, contact info is "  # "[EM" is held back in buffer

    # Chunk 2 finishes tag "AIL_1] now!"
    res2 = buffer.process_delta_text("AIL_1] now!")
    assert res2 == "sarah@skynet.com now!"

    # Final flush
    res3 = buffer.process_delta_text("", is_final=True)
    assert res3 == ""


def test_sse_buffer_synthetic_unbracketed_word_fragmentation():
    """Tests fragmented SSE chunks for synthetic unbracketed entities (e.g., 'Maya' or 'Sarah')."""
    vault = Vault(synthetic=True)
    # Register synthetic mapping manually into the vault
    vault.token_to_original["Maya"] = "OriginalSensitiveName"
    vault.original_to_token["OriginalSensitiveName"] = "Maya"
    vault.max_token_length = len("Maya")

    buffer = SSERehydrationBuffer(vault)

    # Fragmented chunks for 'Maya': ['Hello ', 'M', 'ay', 'a', '! How are you?']
    chunks = ["Hello ", "M", "ay", "a", "! How are you?"]
    emitted = []
    for chunk in chunks:
        out = buffer.process_delta_text(chunk)
        emitted.append(out)

    flush = buffer.process_delta_text("", is_final=True)
    if flush:
        emitted.append(flush)

    assert emitted[0] == "Hello "
    assert emitted[1] == ""  # 'M' held
    assert emitted[2] == ""  # 'May' held
    # A *complete* 'Maya' match at the buffer tail is still boundary-ambiguous:
    # the next chunk could extend it into an unrelated word (e.g. "Mayans"), so
    # it is held rather than rehydrated immediately.
    assert emitted[3] == ""
    assert emitted[4] == "OriginalSensitiveName! How are you?"

    full_output = "".join(emitted)
    assert full_output == "Hello OriginalSensitiveName! How are you?"
    assert "Maya" not in full_output


@pytest.mark.asyncio
async def test_rehydrate_sse_stream_generator():
    """Tests async generator rehydrating SSE stream with split tokens across JSON deltas."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_upstream_stream():
        yield b'data: {"choices":[{"delta":{"content":"User email is [EM"}}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"AIL_1] recorded."}}]}\n'
        yield b"data: [DONE]\n"

    output_bytes = []
    async for chunk in rehydrate_sse_stream(mock_upstream_stream(), vault):
        output_bytes.append(chunk.decode("utf-8"))

    full_output = "".join(output_bytes)
    assert "sarah@skynet.com" in full_output
    assert "[EMAIL_1]" not in full_output


@pytest.mark.asyncio
async def test_anthropic_claude_sse_stream_generator():
    """Tests async generator rehydrating Anthropic Claude SSE delta formats (delta.text)."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_anthropic_stream():
        yield b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"User email is [EM"}}\n\n'
        yield b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"AIL_1] recorded."}}\n\n'
        yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    output_bytes = []
    async for chunk in rehydrate_sse_stream(mock_anthropic_stream(), vault):
        output_bytes.append(chunk.decode("utf-8"))

    full_output = "".join(output_bytes)
    assert "sarah@skynet.com" in full_output
    assert "[EMAIL_1]" not in full_output


async def _collect_stream(raw_stream, vault) -> str:
    """Drains a rehydrated SSE stream into one string."""
    pieces = []
    async for chunk in rehydrate_sse_stream(raw_stream, vault):
        pieces.append(chunk.decode("utf-8"))
    return "".join(pieces)


def _choice_texts(output: str) -> dict:
    """Reassembles each choice's answer from the emitted SSE events, keyed by index."""
    import json

    texts: dict = {}
    for line in output.splitlines():
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices") or ():
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str):
                texts.setdefault(choice.get("index", 0), "")
                texts[choice.get("index", 0)] += content
    return texts


@pytest.mark.asyncio
async def test_parallel_choices_do_not_share_a_retention_window():
    """A token split across one choice's deltas must not leak into another choice.

    With `n` > 1 the choices interleave on one wire and are told apart only by `index`.
    A single shared window released choice 0's held-back "[EMA" into choice 1's next
    event, so choice 0's token was destroyed (never rehydrated) AND choice 1's answer
    carried a fragment of a vault token it never produced.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_parallel_stream():
        yield b'data: {"choices":[{"index":0,"delta":{"content":"A:[EMA"}}]}\n'
        yield b'data: {"choices":[{"index":1,"delta":{"content":"B:hello"}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"content":"IL_1]"}}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_parallel_stream(), vault)

    texts = _choice_texts(output)
    assert texts[0] == "A:sarah@skynet.com"
    assert texts[1] == "B:hello"
    assert "[EMA" not in output


@pytest.mark.asyncio
async def test_every_choice_in_one_event_is_rehydrated():
    """A provider that batches `n` choices into one event must not have the rest skipped.

    `choices` is a list, and only its first entry used to be walked.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_batched_stream():
        yield (
            b'data: {"choices":['
            b'{"index":0,"delta":{"content":"first [EMAIL_1]"}},'
            b'{"index":1,"delta":{"content":"second [EMAIL_1]"}}'
            b"]}\n"
        )
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_batched_stream(), vault)

    texts = _choice_texts(output)
    assert texts[0] == "first sarah@skynet.com"
    assert texts[1] == "second sarah@skynet.com"
    assert "[EMAIL_1]" not in output


@pytest.mark.asyncio
async def test_a_held_tail_flushes_onto_the_choice_that_owns_it():
    """A window still holding text at [DONE] flushes onto its own choice, not choice 0.

    Choice 0 ends mid-token, so its window is still holding at end of stream while
    choice 1 has completed. The flush must be tagged with index 0, and choice 1's
    answer must not have been handed choice 0's held fragment on the way past.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_unterminated_stream():
        yield b'data: {"choices":[{"index":0,"delta":{"content":"A:[EMA"}}]}\n'
        yield b'data: {"choices":[{"index":1,"delta":{"content":"B:[EMAIL_1] done"}}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_unterminated_stream(), vault)

    texts = _choice_texts(output)
    # Choice 1 completed and is untouched by choice 0's held fragment.
    assert texts[1] == "B:sarah@skynet.com done"
    # Choice 0's token never completed, so the held fragment comes back verbatim --
    # on choice 0, which is the point.
    assert texts[0] == "A:[EMA"


def _tool_arguments(output: str) -> dict:
    """Reassembles each tool call's `arguments`, keyed by `(choice index, tool index)`."""
    import json

    args: dict = {}
    for line in output.splitlines():
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices") or ():
            for call in choice.get("delta", {}).get("tool_calls") or ():
                key = (choice.get("index", 0), call.get("index", 0))
                fragment = call.get("function", {}).get("arguments", "")
                args[key] = args.get(key, "") + fragment
    return args


@pytest.mark.asyncio
async def test_streamed_tool_call_arguments_are_rehydrated():
    """A placeholder split across `function.arguments` fragments must be restored.

    Rehydration used to be gated on a string `delta.content`, which a tool-call event
    does not carry, so the whole event was forwarded verbatim and the application
    invoked its tool with `[EMAIL_1]` as the argument value.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_tool_call_stream():
        yield (
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            b'"type":"function","function":{"name":"send_email","arguments":""}}]}}]}\n'
        )
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"to\\": \\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"IL_1]\\"}"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_tool_call_stream(), vault)

    assert _tool_arguments(output)[(0, 0)] == '{"to": "sarah@skynet.com"}'
    assert "[EMAIL_1]" not in output
    assert "[EMA" not in output


@pytest.mark.asyncio
async def test_parallel_tool_calls_do_not_share_a_retention_window():
    """Each tool call accumulates its own `arguments`, so each needs its own window."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_two_tool_calls():
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"a\\":\\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\\"b\\":\\"plain"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"IL_1]\\"}"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"\\"}"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_two_tool_calls(), vault)

    args = _tool_arguments(output)
    assert args[(0, 0)] == '{"a":"sarah@skynet.com"}'
    assert args[(0, 1)] == '{"b":"plain"}'


@pytest.mark.asyncio
async def test_tool_argument_tail_flushes_inside_the_finishing_event():
    """A held tail must ride IN the `finish_reason` event, never in one after it.

    The client concatenates `arguments` fragments and parses the result as JSON the
    moment the choice reports finished. A tail delivered after that point is parsed
    too late, against a truncated document -- which is why this channel cannot reuse
    the trailing-flush behaviour that is harmless for content.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_unterminated_tool_call():
        # Ends mid-token, so the window is still holding when the choice finishes.
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"to\\": \\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_unterminated_tool_call(), vault)

    events = [ln for ln in output.splitlines() if ln.startswith("data: ")]
    finishing = [i for i, ln in enumerate(events) if "finish_reason" in ln]
    assert finishing, "expected a finishing event"
    # The held tail travels in the finishing event itself...
    assert "arguments" in events[finishing[0]]
    # ...and nothing carrying tool arguments follows it.
    assert not [ln for ln in events[finishing[0] + 1 :] if "arguments" in ln]
    assert _tool_arguments(output)[(0, 0)] == '{"to": "[EMA'


@pytest.mark.asyncio
async def test_content_and_tool_arguments_are_separate_channels():
    """One choice's content window must not be fed by its tool-call fragments."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_mixed_stream():
        yield b'data: {"choices":[{"index":0,"delta":{"content":"Sending to [EMA"}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"to\\":\\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"content":"IL_1] now"}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"IL_1]\\"}"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_mixed_stream(), vault)

    assert _choice_texts(output)[0] == "Sending to sarah@skynet.com now"
    assert _tool_arguments(output)[(0, 0)] == '{"to":"sarah@skynet.com"}'


@pytest.mark.asyncio
async def test_restored_tool_arguments_stay_parseable_json():
    """A restored value carrying JSON metacharacters must not break the document.

    `arguments` is JSON text and the client parses the joined fragments, so splicing a
    raw value containing a quote, backslash or newline turns one wrong field into a
    parse error on the whole tool call.
    """
    import json as stdlib_json

    hostile = 'A "quoted" name\\with a backslash\nand a newline\twith a tab'
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token(hostile, "PERSON")

    async def mock_tool_call_stream():
        payload = stdlib_json.dumps('{"who": "' + token + '"}')
        yield (
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
            b'"function":{"arguments":' + payload.encode() + b"}}]}}]}\n"
        )
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_tool_call_stream(), vault)

    arguments = _tool_arguments(output)[(0, 0)]
    # The whole point: it still parses, and the value round-trips exactly.
    assert stdlib_json.loads(arguments) == {"who": hostile}


@pytest.mark.asyncio
async def test_finishing_a_choice_at_the_window_cap_does_not_fail_closed(monkeypatch):
    """Flushing at exactly the cap must not try to open one more window.

    The sibling scan is vault-scoped, so it can borrow a window that was just drained.
    Opening a fresh one on the finishing event would trip the fail-closed cap and cut
    off a stream that was completing normally.
    """
    from llm_shield_proxy.streaming import streaming as streaming_module

    monkeypatch.setattr(streaming_module, "MAX_STREAM_WINDOWS", 2)
    monkeypatch.setattr(streaming_module.settings, "ENABLE_RESPONSE_PII_REDACTION", True)

    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    async def mock_at_cap_stream():
        # Two tool channels fill the cap, and neither event opens a content window.
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"a\\":\\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\\"b\\":\\"[EMA"}}]}}]}\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(mock_at_cap_stream(), vault)

    # The stream completed rather than aborting, and both tails came back.
    assert "[DONE]" in output
    args = _tool_arguments(output)
    assert args[(0, 0)] == '{"a":"[EMA'
    assert args[(0, 1)] == '{"b":"[EMA'


def _anthropic_tool_stream(*partial_json_fragments: str, block_index: int = 0):
    """An Anthropic tool-call stream: block start, input fragments, block stop."""
    import json as stdlib_json

    async def stream():
        start = {
            "type": "content_block_start",
            "index": block_index,
            "content_block": {"type": "tool_use", "id": "toolu_9", "name": "send_email", "input": {}},
        }
        yield b"event: content_block_start\ndata: " + stdlib_json.dumps(start).encode() + b"\n\n"
        for fragment in partial_json_fragments:
            event = {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {"type": "input_json_delta", "partial_json": fragment},
            }
            yield b"event: content_block_delta\ndata: " + stdlib_json.dumps(event).encode() + b"\n\n"
        stop = {"type": "content_block_stop", "index": block_index}
        yield b"event: content_block_stop\ndata: " + stdlib_json.dumps(stop).encode() + b"\n\n"
        yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    return stream()


@pytest.mark.asyncio
async def test_anthropic_streamed_tool_input_is_rehydrated():
    """Anthropic streams tool input as `input_json_delta`, a shape nothing handled.

    `grep input_json_delta` matched nothing before this change, so an Anthropic-native
    tool call reached the caller still carrying placeholders even though the OpenAI
    shape had been fixed.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    output = await _collect_stream(
        _anthropic_tool_stream('{"to": "[EMA', 'IL_1]"}'), vault
    )

    assert _tool_arguments(output)[(0, 0)] == '{"to": "sarah@skynet.com"}'
    assert "[EMAIL_1]" not in output
    assert "[EMA" not in output


@pytest.mark.asyncio
async def test_anthropic_streamed_tool_call_carries_its_id_and_name():
    """An OpenAI-shaped client needs the call's id and name once, on its first event."""
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    output = await _collect_stream(_anthropic_tool_stream('{"to": "[EMAIL_1]"}'), vault)

    import json as stdlib_json

    openers = []
    for line in output.splitlines():
        if not line.startswith("data: ") or "tool_calls" not in line:
            continue
        for call in stdlib_json.loads(line[6:])["choices"][0]["delta"]["tool_calls"]:
            if call.get("id"):
                openers.append(call)
    assert len(openers) == 1, "the call must be opened exactly once"
    assert openers[0]["id"] == "toolu_9"
    assert openers[0]["type"] == "function"
    assert openers[0]["function"]["name"] == "send_email"


@pytest.mark.asyncio
async def test_anthropic_tool_tail_flushes_before_the_block_stops():
    """A held tail must precede `content_block_stop`, not follow it.

    The stop event is where the call finishes growing, so a client parses the joined
    arguments there. This is the Anthropic counterpart of the `finish_reason` rule.
    """
    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    # Ends mid-token, so the window is still holding when the block stops.
    output = await _collect_stream(_anthropic_tool_stream('{"to": "[EMA'), vault)

    events = [ln for ln in output.splitlines() if ln.startswith("data: ")]
    stops = [i for i, ln in enumerate(events) if "content_block_stop" in ln]
    assert stops, "expected a content_block_stop event"
    # Something carrying tool arguments precedes the stop...
    assert any("tool_calls" in ln for ln in events[: stops[0]])
    # ...and nothing carrying them follows it.
    assert not [ln for ln in events[stops[0] + 1 :] if "tool_calls" in ln]
    assert _tool_arguments(output)[(0, 0)] == '{"to": "[EMA'


@pytest.mark.asyncio
async def test_anthropic_streamed_tool_input_is_json_escaped():
    """The Anthropic tool channel is JSON text too, so restored values are escaped."""
    import json as stdlib_json

    hostile = 'Bob "The Man" O\\Brien'
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token(hostile, "PERSON")

    output = await _collect_stream(
        _anthropic_tool_stream('{"who": "' + token + '"}'), vault
    )

    assert stdlib_json.loads(_tool_arguments(output)[(0, 0)]) == {"who": hostile}


@pytest.mark.asyncio
async def test_anthropic_tool_blocks_are_numbered_densely_from_zero():
    """Anthropic numbers tool blocks alongside text blocks; clients expect dense indices.

    A reply whose first block is prose puts its tool call at Anthropic index 1. Emitting
    that verbatim would open a tool call at index 1 with no index 0 in front of it.
    """
    import json as stdlib_json

    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    async def mixed_stream():
        yield (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"Emailing [EMAIL_1] now"}}\n\n'
        )
        start = {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "send_email", "input": {}},
        }
        yield b"event: content_block_start\ndata: " + stdlib_json.dumps(start).encode() + b"\n\n"
        frag = {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"to": "[EMAIL_1]"}'},
        }
        yield b"event: content_block_delta\ndata: " + stdlib_json.dumps(frag).encode() + b"\n\n"
        yield b'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n'
        yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    output = await _collect_stream(mixed_stream(), vault)

    # The tool call is the first one seen, so it is ordinal 0 even though its
    # Anthropic block index is 1.
    assert _tool_arguments(output)[(0, 0)] == '{"to": "sarah@skynet.com"}'
    assert _choice_texts(output)[0] == "Emailing sarah@skynet.com now"


def _sse_events(output: str) -> list:
    """Splits a stream into SSE events the way a compliant client frames them.

    An event ends at a blank line. Two `data:` lines inside one event are ONE payload,
    joined by a newline -- which is why line-by-line parsing cannot see a framing bug.
    """
    return [block for block in output.split("\n\n") if block.strip()]


@pytest.mark.asyncio
async def test_a_flushed_anthropic_tail_is_its_own_sse_event():
    """A buffered flush must terminate before the event it precedes.

    Emitted with a single newline, the flush and the following stop event share the
    upstream's blank line and arrive as one event carrying two newline-joined JSON
    documents. No compliant client can parse that, so the restored tail is lost --
    invisible to any test that reads the stream line by line.
    """
    import json as stdlib_json

    vault = Vault(synthetic=False)
    vault.get_or_create_token("sarah@skynet.com", "EMAIL")  # [EMAIL_1]

    # Ends mid-token, so a tail is still held when the block stops.
    output = await _collect_stream(_anthropic_tool_stream('{"to": "[EMA'), vault)

    for event in _sse_events(output):
        data_lines = [ln for ln in event.splitlines() if ln.startswith("data: ")]
        assert len(data_lines) <= 1, f"event carries {len(data_lines)} data lines: {event!r}"
        for data_line in data_lines:
            # Every payload must be a JSON document on its own.
            stdlib_json.loads(data_line[6:])
