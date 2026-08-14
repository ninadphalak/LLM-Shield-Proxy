import os
import sys
import time
import argparse
import itertools
import threading

# Force UTF-8 output to prevent crashes when LLM returns emojis on Windows PowerShell
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from openai import OpenAI

# We use the local proxy engine components strictly to display what is happening
# in the background. In production, this happens natively inside the proxy.
from llm_shield_proxy.pii_engine import pii_engine
from llm_shield_proxy.vault import Vault, vault_store

# ANSI Colors for beautiful terminal output
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_CYAN = '\033[96m'
C_RESET = '\033[0m'

from pathlib import Path
from dotenv import load_dotenv
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

upstream_url = os.environ.get("UPSTREAM_BASE_URL", "api.openai.com").lower()
if "generativelanguage" in upstream_url:
    default_test_model = "gemini-flash-lite-latest"
elif "deepseek" in upstream_url:
    default_test_model = "deepseek-chat"
else:
    default_test_model = "gpt-3.5-turbo"

def get_cli_args():
    parser = argparse.ArgumentParser(description="Test LLM-Shield-Proxy")
    parser.add_argument("--model", type=str, default=default_test_model, help="Upstream model to test against")
    return parser.parse_known_args()[0]

TARGET_MODEL = default_test_model

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

client = OpenAI(
    api_key=proxy_auth_key, 
    base_url="http://localhost:8000/v1"
)

def run_example(title, prompt, model=TARGET_MODEL):
    # Clear screen for a clean GIF recording frame
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print(f"\n{C_YELLOW}============================================================{C_RESET}")
    print(f"{C_YELLOW}[-] {title}{C_RESET}")
    print(f"{C_YELLOW}============================================================{C_RESET}\n")

    # Explicitly use bracket tag vault for Release 14 GIF
    demo_vault = Vault(synthetic=False)
    redacted_prompt = pii_engine.redact_text(prompt, demo_vault)

    # Dynamically extract all PII discovered by the engine for highlighting
    PII_TERMS = list(demo_vault.original_to_token.keys())

    highlighted_prompt = prompt
    for term in PII_TERMS:
        highlighted_prompt = highlighted_prompt.replace(term, f"{C_GREEN}{term}{C_RESET}")
        
    print(f"\n\n[1] Original Prompt (User -> Proxy):\n\n    {highlighted_prompt}\n")

    # Colorize the tags in the redacted payload (e.g. [PERSON_1]) for the UI
    import re
    redacted_payload_colored = re.sub(r'(\[[A-Z_]+_\d+\])', f"{C_CYAN}\\1{C_RESET}", redacted_prompt)
    print(f"\n\n[2] Redacted Payload (Proxy -> External LLM):\n\n    {redacted_payload_colored}\n")

    try:
        print("\n\n[3] Streaming Re-hydrated Response (Proxy -> User):\n")
        
        import threading
        import itertools
        
        stop_event = threading.Event()
        
        def animate_wait():
            spinner = itertools.cycle([".  ", ".. ", "..."])
            while not stop_event.is_set():
                sys.stdout.write(f"\r    \033[3m(waiting for upstream LLM to process{next(spinner)})\033[0m")
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
                        "content": "You are a helpful assistant. Keep your answers extremely concise, limited to exactly 2 short sentences. You MUST start your response by repeating the EXACT user's full name, ID/SSN, Date of Birth, Email, Phone, and IP address exactly as provided in the prompt. Do not invent any data."
                    },
                    {"role": "user", "content": prompt}
                ],
                stream=True
            )
            
            # Fetch the first chunk to guarantee TTFT has elapsed while animation runs
            chunk_iter = iter(response)
            first_chunk = next(chunk_iter, None)
            
        finally:
            # Stop animation and wait for thread to exit
            stop_event.set()
            t.join()
            
        # Clear the waiting message and prep cursor for the stream
        sys.stdout.write("\r\033[K    ")
        sys.stdout.flush()

        def process_chunk(chunk):
            if chunk and chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                
                # Colorize all known restored PII as it streams in
                for term in PII_TERMS:
                    if term in text:
                        text = text.replace(term, f"{C_GREEN}{term}{C_RESET}")
                
                # Handle newlines for indentation
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    if i > 0:
                        sys.stdout.write(f"\n    ")
                    sys.stdout.write(line)
                sys.stdout.flush()
                
                # Tiny sleep masks erratic network jitter for a perfectly smooth GIF
                time.sleep(0.03)

        process_chunk(first_chunk)
        for chunk in chunk_iter:
            process_chunk(chunk)
            
        # Subtle visual indicator that the stream finished successfully
        sys.stdout.write(f"\n\n    {C_CYAN}[✔] Stream Complete.{C_RESET}\n")
        sys.stdout.flush()
                
    except Exception as e:
        print(f"\n{C_YELLOW}[!] API Error: Ensure proxy is running on port 8000.{C_RESET}")
        print(f"    Error Details: {e}")
        
    print("\n")

prompts = [
    ("TEST 1: HIPAA Compliance (Clinical Data & PII)", 

     "Patient John Smith (DOB: 10/14/1981, SSN: 555-44-3333, MRN: #982341, Phone: 555-019-9537, Email: jsmith81@email.com, Insurance ID: HIX-9928310, IP: 192.168.1.45) was admitted to Mayo Clinic by Dr. House for acute bronchitis and high blood pressure. Check his Azithromycin dose and list what else could cause his cough."),
    
    ("TEST 2: SOC 2 Compliance (Core Banking & KYC)", 
     "Hey, can you help me reconcile the ledger discrepancies for John Smith (DOB: 10/14/1981, SSN: 555-44-3333, Acct #982341, Phone: 555-019-9537, Email: jsmith@bankcorp.com, IP: 192.168.1.45) across our Core Banking system and identify if these unauthorized adjustments breach our risk framework?")
]

if __name__ == "__main__":
    cli_args = get_cli_args()
    TARGET_MODEL = cli_args.model
    for i, (title, prompt) in enumerate(prompts):
        run_example(title, prompt, model=TARGET_MODEL)
        
        # Subtle animated countdown so the terminal doesn't look hung between tests/loops
        for remaining in range(12, 0, -1):
            sys.stdout.write(f"\r    \033[3m(Next test starting in {remaining}s...)\033[0m")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
