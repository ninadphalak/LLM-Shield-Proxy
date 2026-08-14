"""V2 Synthetic Swapping & Zero-Bracket Streaming Demonstration Script.

Used for recording the official V2 demonstration GIF:
- Demonstrates realistic, natural word synthetic swapping (Method B)
- Proves zero Byte-Pair Encoding (BPE) token inflation
- Demonstrates prefix-aware real-time streaming rehydration without brackets
- Incorporates jitter-masking and presentation polish from test_launch.py
"""

import os
import sys
import time
import itertools
import threading
import argparse

# Force UTF-8 output to prevent crashes on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

from llm_shield_proxy.pii_engine import pii_engine
from llm_shield_proxy.vault import Vault

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

# ANSI Colors for rich terminal presentation
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_MAGENTA = "\033[95m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

upstream_url = os.environ.get("UPSTREAM_BASE_URL", "api.openai.com").lower()
if "generativelanguage" in upstream_url:
    default_test_model = "gemini-flash-lite-latest"
elif "deepseek" in upstream_url:
    default_test_model = "deepseek-chat"
else:
    default_test_model = "gpt-4o-mini"

def get_cli_args():
    parser = argparse.ArgumentParser(description="Test LLM-Shield-Proxy V2 Synthetic Swapping")
    parser.add_argument("--model", type=str, default=default_test_model, help="Upstream model to test")
    parser.add_argument("--stream-speed", type=float, default=0.03, help="Delay between streaming tokens for GIF recording")
    return parser.parse_known_args()[0]

TARGET_MODEL = default_test_model
STREAM_SPEED = 0.03

# Automatically resolve a valid key from .env so no manual env setting is required:
valid_keys_env = os.environ.get("VALID_VIRTUAL_KEYS", "")
if valid_keys_env:
    proxy_auth_key = valid_keys_env.split(",")[0].strip()
elif os.environ.get("OPENAI_API_KEY"):
    proxy_auth_key = os.environ.get("OPENAI_API_KEY")
elif os.environ.get("GEMINI_API_KEY"):
    proxy_auth_key = os.environ.get("GEMINI_API_KEY")
else:
    proxy_auth_key = "sk-local-test-key"

# Client connects to local zero-egress proxy
client = OpenAI(
    api_key=proxy_auth_key,
    base_url="http://localhost:8000/v1",
)


def run_synthetic_example(title: str, prompt: str, model: str = TARGET_MODEL):
    """Executes a single synthetic swapping demonstration scenario."""
    # Clear screen for a pristine GIF recording frame
    os.system("cls" if os.name == "nt" else "clear")

    print(f"\n{C_YELLOW}============================================================{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}[-] {title}{C_RESET}")
    print(f"{C_YELLOW}============================================================{C_RESET}\n")

    # Instantiate a synthetic-swapping vault
    demo_vault = Vault(synthetic=True)
    redacted_prompt = pii_engine.redact_text(prompt, demo_vault)

    mapping = demo_vault.original_to_token
    real_pii_terms = list(mapping.keys())

    # 1. Format Original Prompt with Green Highlights
    highlighted_prompt = prompt
    for original, synthetic in mapping.items():
        highlighted_prompt = highlighted_prompt.replace(
            original, f"{C_BOLD}{C_GREEN}{original}{C_RESET}"
        )

    print(f"\n\n[1] Original Prompt (User -> Proxy):\n\n    {highlighted_prompt}\n")

    # 2. Format Synthetic Swapped Egress Payload & Mapping Table
    highlighted_egress = redacted_prompt
    for original, synthetic in mapping.items():
        highlighted_egress = highlighted_egress.replace(
            synthetic, f"{C_BOLD}{C_MAGENTA}{synthetic}{C_RESET}"
        )

    print(f"\n\n[2] Zero-Bracket Egress Payload (Proxy -> External LLM):\n\n    {highlighted_egress}\n")
    print(f"    {C_DIM}Vault Mappings: {', '.join([f'{C_GREEN}{k}{C_RESET} {C_DIM}➔{C_RESET} {C_MAGENTA}{v}{C_RESET}' for k, v in mapping.items()])}{C_RESET}\n")

    # 3. Stream Rehydrated Response
    print("\n\n[3] Streaming Re-hydrated Response (Proxy -> User):\n")

    stop_event = threading.Event()

    def animate_wait():
        spinner = itertools.cycle([".  ", ".. ", "..."])
        while not stop_event.is_set():
            sys.stdout.write(
                f"\r    \033[3m(waiting for upstream LLM to process{next(spinner)})\033[0m"
            )
            sys.stdout.flush()
            time.sleep(0.3)

    t = threading.Thread(target=animate_wait)
    t.start()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert assistant. Keep your answer concise, limited to 2 short sentences. "
                        "You MUST start your response by repeating the EXACT user's full name and location as provided in the prompt."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            stream=True,
        )

        chunk_iter = iter(response)
        first_chunk = next(chunk_iter, None)

    except Exception as e:
        stop_event.set()
        t.join()
        print(f"\n{C_YELLOW}[!] API Error: Ensure proxy is running on port 8000.{C_RESET}")
        print(f"    Error Details: {e}\n")
        return

    finally:
        stop_event.set()
        t.join()

    # Clear wait indicator and prep cursor for the stream
    sys.stdout.write("\r\033[K    ")
    sys.stdout.flush()

    def process_chunk(chunk):
        if chunk and chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content

            # Colorize re-hydrated PII in green as it streams
            for original in real_pii_terms:
                if original in text:
                    text = text.replace(
                        original, f"{C_BOLD}{C_GREEN}{original}{C_RESET}"
                    )

            # Handle newlines for clean 4-space indentation
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if i > 0:
                    sys.stdout.write("\n    ")
                sys.stdout.write(line)
            sys.stdout.flush()

            # Tiny sleep masks network jitter for a smooth GIF recording
            time.sleep(STREAM_SPEED)

    process_chunk(first_chunk)
    for chunk in chunk_iter:
        process_chunk(chunk)

    # Subtle visual indicator that stream completed cleanly
    sys.stdout.write(f"\n\n    {C_CYAN}[✔] Zero-Bracket Stream Complete.{C_RESET}\n")
    sys.stdout.flush()


scenarios = [
    (
        "SCENARIO 1: Clinical & Healthcare Privacy (HIPAA Compliance)",
        "Write a concise clinical summary for my patient, John Doe, who lives in San Francisco, California.",
    ),
    (
        "SCENARIO 2: Enterprise Identity Verification (SOC 2 Compliance)",
        "Draft an identity verification record for Dr. Jane Smith (SSN: 123-45-6789, email: jane.smith@acmehealth.com, phone: 555-0199) who visited our Boston clinic.",
    ),
]


def main():
    global TARGET_MODEL, STREAM_SPEED
    cli_args = get_cli_args()
    TARGET_MODEL = cli_args.model
    STREAM_SPEED = cli_args.stream_speed

    for i, (title, prompt) in enumerate(scenarios):
        run_synthetic_example(title, prompt, model=TARGET_MODEL)

        if i < len(scenarios) - 1:
            # Animated countdown so the terminal doesn't look hung between tests/loops
            for remaining in range(10, 0, -1):
                sys.stdout.write(
                    f"\r    \033[3m(Next scenario starting in {remaining}s...)\033[0m"
                )
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
