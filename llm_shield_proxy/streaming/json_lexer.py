from typing import List, Tuple

class StreamingJSONLexer:
    """Non-blocking, O(1) memory Streaming JSON Lexer for identifying maskable values.
    
    Operates strictly as a Finite State Machine (FSM) across chunk boundaries.
    """
    
    STATE_ROOT = 0
    STATE_IN_KEY = 1
    STATE_WAIT_COLON = 2
    STATE_IN_VALUE_STRING = 3
    STATE_IN_VALUE_NON_STRING = 4
    STATE_ESCAPE = 5

    def __init__(self):
        self.state = self.STATE_ROOT
        self.prev_string_state = None
        self.expecting_value = False

    def feed_chunk(self, chunk_text: str) -> List[Tuple[str, bool]]:
        """Processes a chunk of JSON text and yields token slices.
        
        Args:
            chunk_text: The incoming text chunk.
            
        Returns:
            List of (token_text, is_maskable) tuples.
        """
        tokens = []
        start_idx = 0
        i = 0
        length = len(chunk_text)
        
        while i < length:
            char = chunk_text[i]
            
            if self.state == self.STATE_ROOT:
                if char == '"':
                    if i > start_idx:
                        tokens.append((chunk_text[start_idx:i], False))
                    
                    if self.expecting_value:
                        self.state = self.STATE_IN_VALUE_STRING
                        tokens.append(('"', False))
                        start_idx = i + 1
                    else:
                        self.state = self.STATE_IN_KEY
                        start_idx = i
                elif char == ':':
                    self.expecting_value = True
                elif char in ('{', ','):
                    self.expecting_value = False
                elif char == '[':
                    self.expecting_value = True
                elif char in ('}', ']'):
                    self.expecting_value = False
                elif char not in (' ', '\n', '\r', '\t'):
                    if i > start_idx:
                        tokens.append((chunk_text[start_idx:i], False))
                    start_idx = i
                    self.state = self.STATE_IN_VALUE_NON_STRING
                    continue
                i += 1
                
            elif self.state == self.STATE_IN_KEY:
                next_quote = chunk_text.find('"', i)
                next_esc = chunk_text.find('\\', i)
                
                if next_quote == -1 and next_esc == -1:
                    i = length
                elif next_quote != -1 and (next_esc == -1 or next_quote < next_esc):
                    i = next_quote
                    self.state = self.STATE_WAIT_COLON
                    tokens.append((chunk_text[start_idx:i+1], False))
                    start_idx = i + 1
                    i += 1
                else:
                    i = next_esc
                    self.prev_string_state = self.STATE_IN_KEY
                    self.state = self.STATE_ESCAPE
                    i += 1
                    
            elif self.state == self.STATE_WAIT_COLON:
                if char == ':':
                    self.state = self.STATE_ROOT
                    self.expecting_value = True
                elif char not in (' ', '\n', '\r', '\t'):
                    self.state = self.STATE_ROOT
                    continue
                i += 1
                
            elif self.state == self.STATE_IN_VALUE_STRING:
                next_quote = chunk_text.find('"', i)
                next_esc = chunk_text.find('\\', i)
                
                if next_quote == -1 and next_esc == -1:
                    i = length
                elif next_quote != -1 and (next_esc == -1 or next_quote < next_esc):
                    i = next_quote
                    self.state = self.STATE_ROOT
                    self.expecting_value = False
                    if i > start_idx:
                        tokens.append((chunk_text[start_idx:i], True))
                    tokens.append(('"', False))
                    start_idx = i + 1
                    i += 1
                else:
                    i = next_esc
                    self.prev_string_state = self.STATE_IN_VALUE_STRING
                    self.state = self.STATE_ESCAPE
                    if i > start_idx:
                        tokens.append((chunk_text[start_idx:i], True))
                    tokens.append(('\\', True))
                    start_idx = i + 1
                    i += 1
                    
            elif self.state == self.STATE_IN_VALUE_NON_STRING:
                if char in (' ', '\n', '\r', '\t', ',', '}', ']'):
                    self.state = self.STATE_ROOT
                    if i > start_idx:
                        tokens.append((chunk_text[start_idx:i], True))
                    start_idx = i
                    continue
                i += 1
                
            elif self.state == self.STATE_ESCAPE:
                self.state = self.prev_string_state
                if self.state == self.STATE_IN_VALUE_STRING:
                    tokens.append((char, True))
                    start_idx = i + 1
                i += 1

        if start_idx < length:
            is_maskable = self.state in (self.STATE_IN_VALUE_STRING, self.STATE_IN_VALUE_NON_STRING)
            tokens.append((chunk_text[start_idx:length], is_maskable))
            
        return tokens
