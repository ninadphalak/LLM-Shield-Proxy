import os
import psutil

# Import the main FastAPI app to load all application overhead
from llm_shield_proxy.api.main import app

# Import V3 components to ensure they are loaded and initialized
from llm_shield_proxy.v3.crypto import StatelessPIICipher
from llm_shield_proxy.v3.streaming_lexer import StatelessStreamingLexer
from llm_shield_proxy.engines.pii_engine import pii_engine

# Initialize the components to ensure they are loaded in memory
DUMMY_KEY = b"0" * 32
cipher = StatelessPIICipher(key=DUMMY_KEY, version=1, session_id="mem_test")
lexer = StatelessStreamingLexer(cipher)
pii_engine.detect_spans("test string")

# Get memory footprint
process = psutil.Process(os.getpid())
rss_mb = process.memory_info().rss / (1024 * 1024)
print(f"Verified Bare Process Memory Footprint RSS: {rss_mb:.2f} MB")
