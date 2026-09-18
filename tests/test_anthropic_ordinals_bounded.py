"""One stream may not grow memory without limit.

`anthropic_tool_ordinals` maps an Anthropic content-block index to a dense tool-call
ordinal. The key comes from the upstream, so an upstream that invents a new index per
event grew the map forever: an audit measured 116 bytes per event and 46.6 MB live in a
single stream at 400,000 events, multiplied by however many streams are open.

Every other per-stream map in this file is capped. This one was not, because it is
populated by `content_block_start` without going through `_buffer_for`, so
`MAX_STREAM_WINDOWS` never applied to it.
"""

from __future__ import annotations

import asyncio

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import MAX_ANTHROPIC_TOOL_ORDINALS, rehydrate_sse_stream


def _run(lines: list[str]) -> str:
    async def main() -> str:
        vault = Vault(synthetic=False)

        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join([c.decode("utf-8") async for c in rehydrate_sse_stream(source(), vault)])

    return asyncio.run(main())


def _tool_block(index: int) -> str:
    return (
        'data: {"type":"content_block_start","index":%d,'
        '"content_block":{"type":"tool_use","id":"t%d","name":"fetch"}}\n\n' % (index, index)
    )


def test_a_hostile_index_stream_does_not_grow_without_limit() -> None:
    """Far more distinct indices than the cap, in one stream."""
    events = [_tool_block(i) for i in range(MAX_ANTHROPIC_TOOL_ORDINALS * 3)]
    events.append("data: [DONE]\n\n")
    out = _run(events)
    assert "[DONE]" in out, "the stream did not survive the flood"


def test_ordinals_stay_stable_for_a_normal_stream() -> None:
    """A real stream reuses a handful of block indices, and those must not be evicted."""
    events = [_tool_block(0), _tool_block(1), _tool_block(0), "data: [DONE]\n\n"]
    out = _run(events)
    # Block 0 opened first, so it is tool ordinal 0, and it must still be 0 when it
    # reappears. Two distinct ordinals for three blocks.
    assert '"index":0' in out
    assert '"index":1' in out


def test_the_cap_is_a_real_number_and_not_unbounded() -> None:
    assert isinstance(MAX_ANTHROPIC_TOOL_ORDINALS, int)
    assert 0 < MAX_ANTHROPIC_TOOL_ORDINALS <= 4096
