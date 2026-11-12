"""Response redaction under the stream shapes real providers produce.

Most tests in this suite cut a value once, into two chunks. Real providers do not deliver
text that way. Many send about one tokenizer token per event, so a value arrives in many
small pieces, and a piece often ends right after a `.`, `?` or `!` inside the value
(`555.`, `10.`). Others batch a few tokens per event. Several detectors run on the same
stream at once, next to the restore of the caller's own values.

Two defects were found with these shapes and are pinned here:

1. With an empty vault (the request carried no PII, so nothing was masked), the streamed
   response scan was skipped for everything but the held-back tail. Every model-produced
   value before the last word reached the client, even when it arrived in one chunk.
2. The hold-back kept only the text after the last space. A value that contains spaces (a
   card or IBAN printed in groups) left one group at a time when chunks ended at those
   spaces, as a one-token-per-event stream does, and no group matched a detector alone.

The oracle throughout is the non-streaming scan of the whole text.
"""

from __future__ import annotations

import asyncio
import json
import random
import re

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import (
    SSERehydrationBuffer,
    redact_model_originated_text,
    rehydrate_sse_stream,
)

USER_EMAIL = "lylyfwzv@example.com"

# One token per event, as a byte-pair tokenizer of a widely used provider splits these
# sentences. Hard-coded so the test needs no tokenizer package.
TOKEN_STREAMS = {
    "spaced card and IPv4": [
        "Okay",
        ".",
        " First",
        " use",
        " ",
        "411",
        "1",
        " ",
        "572",
        "7",
        " ",
        "812",
        "4",
        " ",
        "559",
        "8",
        ",",
        " then",
        " use",
        " ",
        "10",
        ".",
        "239",
        ".",
        "243",
        ".",
        "12",
        " if",
        " the",
        " first",
        " fails",
        ".",
    ],
    "spaced IBAN": [
        "Pay",
        " DE",
        "89",
        " ",
        "370",
        "4",
        " ",
        "004",
        "4",
        " ",
        "053",
        "2",
        " ",
        "013",
        "0",
        " ",
        "00",
        " today",
        ".",
    ],
    "dotted phone": [
        "Sure",
        ".",
        " You",
        " can",
        " call",
        " me",
        " at",
        " ",
        "555",
        ".",
        "867",
        ".",
        "530",
        "9",
        " any",
        " time",
        " today",
        ".",
    ],
    "JWT": [
        "Sure",
        ".",
        " The",
        " value",
        " is",
        " ey",
        "J",
        "hb",
        "G",
        "ci",
        "Oi",
        "JI",
        "Uz",
        "I",
        "1",
        "Ni",
        "J",
        "9",
        ".",
        "ey",
        "J",
        "zd",
        "WI",
        "i",
        "Oi",
        "Ix",
        "Mj",
        "M",
        "0",
        "In",
        "0",
        ".",
        "abc",
        "123",
        "def",
        "456",
        " and",
        " nothing",
        " else",
        ".",
    ],
}

# Values the whole-text scan redacts, several with `.`, `?` or `!` inside, several with spaces.
VALUES = [
    "555.867.5309",
    "+1.415.555.0132",
    "192.168.10.24",
    "203.0.113.77",
    "john.smith@example.com",
    "a.b.c@mail.example.co.uk",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456",
    "030.1234.5678",
    "219-09-9999",
    "4111 1111 1111 1111",
    "DE89 3704 0044 0532 0130 00",
]
PLACEMENTS = [
    "Sure. The value you asked for is {} and nothing else.",
    "{} is the one on file.",
    "Call {}. Thanks!",
    "Is it {}? Yes.",
]


@pytest.fixture
def response_redaction_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True)


def _vault(empty: bool) -> tuple[Vault, str]:
    vault = Vault()
    if empty:
        return vault, ""
    token = vault.get_or_create_token(USER_EMAIL, "EMAIL")
    return vault, f"You sent: {token}. "


def _drive(chunks, vault, canary=None) -> str:
    buffer = SSERehydrationBuffer(vault, canary=canary)
    out = "".join(buffer.process_delta_text(chunk) for chunk in chunks)
    return out + buffer.process_delta_text("", is_final=True)


def _whole(text: str, vault: Vault) -> str:
    return vault.rehydrate(redact_model_originated_text(text, vault), retention_length=0)


def _words_and_punctuation(text: str) -> list:
    """A tokenizer-like split with no dependency: words, single punctuation marks, spaces
    attached to the following word."""
    return re.findall(r" ?[A-Za-z0-9]+| ?[^A-Za-z0-9 ]| ", text)


def _random_chunks(text: str, rng: random.Random) -> list:
    out, i = [], 0
    while i < len(text):
        n = rng.randint(1, 12)
        out.append(text[i : i + n])
        i += n
    return out


def _cuts_after_punctuation(text: str, value: str):
    start = text.index(value)
    for offset, char in enumerate(value[:-1]):
        if char in ".?!":
            yield start + offset + 1


def _shapes(text: str, value: str, rng: random.Random):
    """Token-aligned, batched, punctuation-terminated and many-chunk streams of `text`."""
    tokens = _words_and_punctuation(text)
    assert "".join(tokens) == text
    yield "one chunk", [text]
    yield "one token per chunk", tokens
    for n in (2, 3, 4):
        yield f"{n} tokens per chunk", ["".join(tokens[i : i + n]) for i in range(0, len(tokens), n)]
    yield "one character per chunk", list(text)
    for _ in range(10):
        yield "random 1-12 characters", _random_chunks(text, rng)
    for pos in _cuts_after_punctuation(text, value):
        yield "chunk ends after punctuation in the value", [text[:pos], text[pos:]]
        start = text.index(value)
        if start:
            yield "prefix, value up to punctuation, rest", [text[:start], text[start:pos], text[pos:]]
        cut, p = [], 0
        for token in tokens:
            if p < pos < p + len(token):
                cut += [token[: pos - p], token[pos - p :]]
            else:
                cut.append(token)
            p += len(token)
        yield "token stream cut after punctuation in the value", cut
    groups = value.split(" ")
    if len(groups) > 1:
        start = text.index(value)
        pieces = [text[:start]] + [g + " " for g in groups[:-1]] + [groups[-1] + text[start + len(value) :]]
        yield "one value group per chunk", pieces


@pytest.mark.parametrize("empty_vault", [True, False], ids=["empty-vault", "vault-in-use"])
@pytest.mark.parametrize("name", sorted(TOKEN_STREAMS))
def test_token_streams_match_whole_text_redaction(name, empty_vault, response_redaction_on):
    vault, prefix = _vault(empty_vault)
    chunks = ([prefix] if prefix else []) + TOKEN_STREAMS[name]
    text = "".join(chunks)
    expected = _whole(text, vault)
    assert "_REDACTED]" in expected, "precondition: the whole-text scan must redact something here"
    assert _drive(chunks, vault) == expected


@pytest.mark.parametrize("empty_vault", [True, False], ids=["empty-vault", "vault-in-use"])
@pytest.mark.parametrize("value", VALUES)
def test_no_stream_shape_leaks_a_value_the_whole_text_scan_redacts(value, empty_vault, response_redaction_on):
    rng = random.Random(value)
    for placement in PLACEMENTS:
        vault, prefix = _vault(empty_vault)
        text = prefix + placement.format(value)
        expected = _whole(text, vault)
        assert value not in expected, f"precondition: {value!r} must be redacted when scanned whole"
        for shape, chunks in _shapes(text, value, rng):
            seen = _drive(chunks, vault)
            assert value not in seen, f"{shape} leaked {value!r}: {chunks!r} -> {seen!r}"
            assert value.replace(" ", "") not in seen.replace(" ", ""), f"{shape} leaked {value!r}"
            if prefix:
                assert USER_EMAIL in seen, f"{shape} lost the caller's own value"


def test_minimal_spaced_card_one_group_per_chunk(response_redaction_on):
    """Defect 2, smallest case: four chunks, each ending at a space inside the card."""
    vault, _ = _vault(empty=False)
    seen = _drive(["Use 4111 ", "1111 ", "1111 ", "1111 now."], vault)
    assert "4111 1111 1111 1111" not in seen
    assert seen == "Use [CREDIT_CARD_REDACTED] now."


def test_minimal_empty_vault_single_chunk(response_redaction_on):
    """Defect 1, smallest case: no PII in the request, one chunk, a value before the last word."""
    vault, _ = _vault(empty=True)
    seen = _drive(["Your SSN is 219-09-9999 and more"], vault)
    assert "219-09-9999" not in seen


def test_several_detectors_and_the_restore_on_one_stream(response_redaction_on):
    """Card, IPv4, email and phone detectors fire on one token stream while the caller's own
    value is restored and the canary tripwire is armed."""
    rng = random.Random(7)
    values = ["4111 5727 8124 5598", "10.239.243.12", "peter.lopez@example.com", "555.867.5309"]
    for _ in range(25):
        a, b = rng.sample(values, 2)
        vault, prefix = _vault(empty=False)
        text = f"{prefix}First {a}. Then {b}! Done?"
        expected = _whole(text, vault)
        for chunks in (_words_and_punctuation(text), _random_chunks(text, rng), list(text)):
            seen = _drive(chunks, vault, canary="CANARY-7f3a9c")
            assert seen == expected, chunks


def test_hold_back_stays_bounded_with_spaced_values(response_redaction_on):
    vault, _ = _vault(empty=False)
    buffer = SSERehydrationBuffer(vault)
    for _ in range(100):
        buffer.process_delta_text("card 4111 ")
        buffer.process_delta_text("1111 ")
        assert len(buffer.content_buffer) <= settings.RESPONSE_PII_SCAN_WINDOW + 64


def _sse(field: str, chunks) -> list:
    return [
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {field: chunk}}]}) + "\n\n" for chunk in chunks
    ] + ["data: [DONE]\n\n"]


def _sse_text(lines, vault, field: str) -> str:
    async def main() -> str:
        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join([c.decode("utf-8") async for c in rehydrate_sse_stream(source(), vault)])

    out = ""
    for line in asyncio.run(main()).splitlines():
        if line.startswith("data: {"):
            for choice in json.loads(line[6:]).get("choices", []):
                piece = (choice.get("delta") or {}).get(field)
                if isinstance(piece, str):
                    out += piece
    return out


@pytest.mark.parametrize("field", ["content", "reasoning_content"])
@pytest.mark.parametrize("name", sorted(TOKEN_STREAMS))
def test_sse_token_stream_with_an_empty_vault(name, field, response_redaction_on):
    vault = Vault(synthetic=False)
    chunks = TOKEN_STREAMS[name]
    text = "".join(chunks)
    seen = _sse_text(_sse(field, chunks), vault, field)
    assert seen == redact_model_originated_text(text, vault)


@pytest.mark.parametrize("empty_vault", [True, False], ids=["empty-vault", "vault-in-use"])
@pytest.mark.parametrize("name", sorted(TOKEN_STREAMS))
def test_anthropic_thinking_stream_is_scanned_like_text(name, empty_vault, response_redaction_on):
    """Thinking deltas are ordered prose. Scanned one event at a time, a value sent one token
    per event reached the client whole."""
    vault = Vault(synthetic=False)
    if not empty_vault:
        vault.get_or_create_token(USER_EMAIL, "EMAIL")
    chunks = TOKEN_STREAMS[name]
    lines = [
        "event: content_block_delta\ndata: "
        + json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": c}})
        + "\n\n"
        for c in chunks
    ]
    lines.append('event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n')
    lines.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')

    async def main() -> str:
        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join([c.decode("utf-8") async for c in rehydrate_sse_stream(source(), vault, path="v1/messages")])

    seen = ""
    for line in asyncio.run(main()).splitlines():
        if line.startswith("data: {"):
            event = json.loads(line[6:])
            seen += (event.get("delta") or {}).get("thinking", "") or ""
    text = "".join(chunks)
    assert seen == redact_model_originated_text(text, vault)


def _final_call_vault(kind: str):
    if kind == "stateless":
        from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault

        return StatelessCryptoVault()
    vault, _ = _vault(empty=kind == "empty")
    return vault


@pytest.mark.parametrize("kind", ["empty", "in-use", "stateless"])
@pytest.mark.parametrize(
    "chunks",
    [
        ["Your SSN is 219-09-9999 ok"],
        ["Here is a sentence long enough to pass the scan window on its own. ", "SSN 219-09-9999"],
        ["Card 4111 ", "1111 ", "1111 ", "1111"],
    ],
    ids=["one-chunk", "value-in-last-chunk", "spaced-groups"],
)
def test_a_final_call_that_carries_text_scans_its_tail(kind, chunks, response_redaction_on):
    """The ext_proc body hook passes the last body chunk with `is_final=True`. The tail held
    back on that call has never been scanned, so it must be scanned before it leaves, not
    emitted as it is."""
    vault = _final_call_vault(kind)
    buffer = SSERehydrationBuffer(vault)
    out = "".join(buffer.process_delta_text(chunk) for chunk in chunks[:-1])
    out += buffer.process_delta_text(chunks[-1], is_final=True)
    assert "219-09-9999" not in out
    assert "4111 1111 1111 1111" not in out
    assert "_REDACTED]" in out


@pytest.mark.parametrize("blob", ["".join(str(i % 10) for i in range(6000)), "QUJD" * 1500], ids=["digit-run", "base64"])
def test_a_match_that_keeps_growing_does_not_grow_the_hold(blob, response_redaction_on):
    """An open-ended match (a long digit run or encoded blob streamed a few characters at a
    time) must not pin the hold at its start: the buffer, and the scan of it on every event,
    would grow with the stream."""
    buffer = SSERehydrationBuffer(Vault(synthetic=False))
    window = settings.RESPONSE_PII_SCAN_WINDOW
    for i in range(0, len(blob), 3):
        buffer.process_delta_text(blob[i : i + 3])
        assert len(buffer.content_buffer) <= 2 * window + 3
    buffer.process_delta_text("", is_final=True)
