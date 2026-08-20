import json
from typing import Dict

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.json_lexer import StreamingJSONLexer
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer


class MockVault(Vault):
    def __init__(self):
        super().__init__("mock_session")
        self.token_to_original: Dict[str, str] = {"dummy": "dummy"}

    def mask(self, text: str) -> str:
        return text

    def rehydrate(self, text: str, retention_length: int = 0) -> str:
        # Mock behavior: replace the target value if found in the buffered chunk
        if "123-45-6789" in text:
            return text.replace("123-45-6789", "[MASKED_SSN]")
        return text


def test_streaming_fragmented_tool_calls():
    """
    Test streaming fragmented tool calls with escaped quotes.
    Ensures keys are unredacted and values are properly identified for masking/rehydration.
    """
    # Simulate chunks splitting on escaped quotes
    chunks = ['{"arguments": "{\\"ssn\\": \\"', "123-45-6789", '\\"}"}']

    vault = MockVault()
    buffer = SSERehydrationBuffer(vault)

    assembled_stream = ""
    for i, chunk in enumerate(chunks):
        is_final = i == len(chunks) - 1
        emitted = buffer.process_delta_text(chunk, is_final=is_final)
        assembled_stream += emitted

    # Assert that "ssn" (the key) remains unredacted
    assert '\\"ssn\\"' in assembled_stream, "Key 'ssn' was improperly redacted or corrupted"

    # Assert that "123-45-6789" (the value) is masked/rehydrated properly
    assert "[MASKED_SSN]" in assembled_stream, "Value was not masked/rehydrated properly"
    assert "123-45-6789" not in assembled_stream, "Value leaked unmasked"

    # Assert that downstream json.loads() on the fully assembled stream succeeds with valid syntax
    try:
        parsed = json.loads(assembled_stream)
        assert parsed["arguments"] == '{"ssn": "[MASKED_SSN]"}'
    except json.JSONDecodeError as e:
        pytest.fail(f"JSON syntax corrupted during stream processing: {e}\\nAssembled stream: {assembled_stream}")


def test_json_lexer_state_machine_simple():
    lexer = StreamingJSONLexer()
    tokens = lexer.feed_chunk('{"key": "value", "arr": [1, 2, 3]}')

    # Verify keys are False and values are True
    assert tokens[0] == ("{", False)
    assert tokens[1] == ('"key"', False)
    assert tokens[2] == (": ", False)
    assert tokens[3] == ('"', False)
    assert tokens[4] == ("value", True)
    assert tokens[5] == ('"', False)
    assert tokens[6] == (", ", False)
    assert tokens[7] == ('"arr"', False)
    assert tokens[8] == (": [", False)
    assert tokens[9] == ("1", True)
    assert tokens[10] == (", ", False)
    assert tokens[11] == ("2", True)
    assert tokens[12] == (", ", False)
    assert tokens[13] == ("3", True)
    assert tokens[14] == ("]}", False)


def test_json_lexer_escaped_backslash_handling():
    """
    Test Escaped Backslash (\\\\) Handling.
    A prompt contains a literal Windows path or regex like C:\\Users\\JohnDoe.
    Validation: When the lexer hits the first \\, it enters STATE_ESCAPE.
    The second \\ consumes the escape and must return the lexer to STATE_IN_VALUE_STRING.
    If followed by a quote ", that quote must correctly be recognized as the string terminator.
    """
    lexer = StreamingJSONLexer()
    # JSON string: {"path": "C:\\\\Users\\\\JohnDoe"}
    tokens = lexer.feed_chunk('{"path": "C:\\\\Users\\\\JohnDoe"}')

    # We want to ensure that the value correctly closes on the final quote.
    # tokens should end with the closing quote as structural (False) and then closing brace (False).
    # Since tokens can be fragmented (like individual backslashes being emitted), we just check the structure.

    # Ensure that "C:\\\\Users\\\\JohnDoe" is captured as is_maskable=True fragments.
    maskable_text = "".join([t[0] for t in tokens if t[1]])
    assert maskable_text == "C:\\\\Users\\\\JohnDoe", "Escaped backslashes corrupted maskable value"

    # Check that the last tokens properly terminate the string and the object
    unmaskable_text = "".join([t[0] for t in tokens if not t[1]])
    assert unmaskable_text == '{"path": ""}', "Structural JSON integrity lost due to backslash escape mishandling"

    # Specifically assert that the lexer ended up in ROOT state, meaning the quote successfully terminated the string
    assert lexer.state == StreamingJSONLexer.STATE_ROOT


def test_json_lexer_nested_objects():
    """
    Test Nested Objects within Values.
    The Scenario: Structured outputs where a value is a sub-object (e.g., {"meta": {"user_id": 123}})
    Validation: The opening inner brace { must flip expecting_value = False so the nested "user_id"
    is recognized as an immutable key rather than a maskable value.
    """
    lexer = StreamingJSONLexer()
    tokens = lexer.feed_chunk('{"meta": {"user_id": 123}}')

    # Validate "meta" is a key
    assert ('"meta"', False) in tokens

    # Validate "user_id" is a key (False, not True)
    assert ('"user_id"', False) in tokens

    # Validate 123 is a value (True)
    assert ("123", True) in tokens

    # The JSON structure should be perfectly preserved in unmaskable tokens
    # excluding the maskable value "123"
    unmaskable_text = "".join([t[0] for t in tokens if not t[1]])
    assert unmaskable_text == '{"meta": {"user_id": }}'
