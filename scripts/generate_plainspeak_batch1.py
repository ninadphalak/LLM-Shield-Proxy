import os

docs_dir = "docs/features"

plainspeak_data = {
    "data-protection-pii-redaction/tier-2-shannon-entropy-scanner.md": """
## Plainspeak
This feature acts like a randomness detector. While some sensitive information (like phone numbers) has a predictable format, things like passwords or secret API keys just look like random gibberish.

Because we can't search for a specific password pattern, this scanner mathematically measures how "random" a piece of text is (known as Shannon entropy). If it spots a string of text that is completely unpredictable and random, it flags it as a likely secret key and hides it to prevent accidental leaks.
""",
    
    "data-protection-pii-redaction/tier-3-quantized-onnx-bert-ner.md": """
## Plainspeak
This feature is a highly efficient artificial intelligence reader. Instead of just looking for strict patterns like 9-digit numbers, it actually reads the surrounding sentence to understand the context. 

For example, it can tell the difference between "Call Mr. Ford" (a person's name) and "I drive a Ford" (a car brand). To ensure it runs lightning-fast without slowing down your system, the AI model has been stripped down to its essential math (quantized) and runs directly in the computer's memory.
""",
    
    "data-protection-pii-redaction/v3-stateless-ast-aware-semantic-pii-firewall.md": """
## Plainspeak
This feature acts as a smart translator between an AI agent and the tools it uses (like a database or a calculator). 

When an AI wants to use a tool, it sends instructions in a specific computer format (called JSON). If we just blindly blacked-out sensitive words in those instructions, it would break the formatting and cause the tool to crash. Instead, this firewall carefully unpackages the instructions, hides only the sensitive data while keeping the structure intact, and then seamlessly repackages it so the tool still works perfectly.
""",
    
    "data-protection-pii-redaction/format-preserving-synthetic-masking-entropy.md": """
## Plainspeak
This feature creates realistic fake data to replace sensitive information. 

If you just replace a real name with "[CENSORED]", the AI reading it might get confused because the sentence structure is suddenly unnatural. Instead, this feature automatically swaps out a real name for a fake name (like replacing "John Doe" with "Alex Smith"), or a real credit card with a mathematically valid fake credit card. This keeps the AI completely oblivious to the fact that the data was redacted, allowing it to generate much better responses.
""",
    
    "data-protection-pii-redaction/in-band-stateless-cryptographic-masking.md": """
## Plainspeak
This feature allows you to securely hide sensitive data and retrieve it later, without ever having to save it to a database.

Normally, to hide a name and restore it later, you have to store the real name in a secure vault somewhere. Instead, this feature mathematically scrambles the real name using a master password and replaces the text directly in the message with the scrambled version. When the message comes back, it uses the master password to unscramble it. This means there is zero risk of a database being hacked, because no database is used!
""",
    
    "data-protection-pii-redaction/stateless-redis-ttl-vault.md": """
## Plainspeak
This feature provides a highly secure, temporary storage locker for sensitive information. 

When a user shares a sensitive detail (like their medical condition), this vault locks it away and replaces it with a temporary placeholder token (like `TOKEN_123`). The AI only sees the placeholder token. The brilliant part is that the locker has an automatic self-destruct timer (TTL). Once the conversation is over, the vault automatically deletes the sensitive data forever, guaranteeing it isn't left sitting on a server indefinitely.
""",
    
    "data-protection-pii-redaction/granular-entity-policy-scopes.md": """
## Plainspeak
This feature ensures that different departments have exactly the right level of data security tailored to their needs, rather than using a one-size-fits-all approach.

For example, the HR department's AI might be allowed to see employee names, but the Marketing department's AI should definitely not. This feature creates specific "ID badges" (profiles) for different teams. When a team uses the system, it instantly checks their badge and strictly applies their custom rules, defaulting to blocking everything if it's ever unsure.
""",

    "data-protection-pii-redaction/4-mode-per-request-masking-pipeline.md": """
## Plainspeak
This feature gives you the ultimate flexibility to choose exactly how sensitive data is hidden on a case-by-case basis. 

Instead of being locked into one method, you can tell the system what to do for each individual request. You can choose to replace the data with a realistic fake (Synthetic), a standard placeholder tag, completely black it out (Scrub), or scramble it with a password so you can read it later (Crypto). This means developers have full control over the privacy technique they want to use at any given moment.
"""
}

def process_batch():
    for rel_path, plainspeak_text in plainspeak_data.items():
        filepath = os.path.join(docs_dir, rel_path)
        if not os.path.exists(filepath):
            print(f"Error: {filepath} not found.")
            continue
            
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if "## Plainspeak" in content:
            print(f"Skipping {rel_path} - already has Plainspeak.")
            continue
            
        # Append the text just before ## Related Tests if it exists, otherwise at the end.
        if "## Related Tests" in content:
            new_content = content.replace("## Related Tests", plainspeak_text.strip() + "\n\n## Related Tests")
        else:
            new_content = content.strip() + "\n\n" + plainspeak_text.strip() + "\n"
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        print(f"Successfully appended Plainspeak to {rel_path}")

if __name__ == "__main__":
    process_batch()
