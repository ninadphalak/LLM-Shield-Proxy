
with open('ARCHITECTURE.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix Tier 2 header
content = content.replace(
    '### Tier 2: Shannon Entropy & Format-Preserving Synthetic Masking',
    '### Tier 2: Shannon Entropy (Unstructured Secrets)\n* **Implementation Mechanics:** Regular expressions fail on unstructured data (e.g., 64-character raw cryptographic keys). The Tier 2 engine computes Shannon entropy across a sliding window. It targets base64 strings with entropy >= 4.5 bits/char and hex strings >= 3.4 bits/char.\n* **Flags:** [`ENABLE_TIER2_ENTROPY`](DEPLOYMENT.md)\n\n### Step 2 of Masking: Format-Preserving Synthetic Masking\n* **Format-Preserving Masking:** Instead of returning bracketed `[API_KEY_1]`, the engine uses `canonical locale substitution` to generate synthetic equivalents (e.g., swapping a real SSN for a valid but fake SSN format). This preserves LLM token-attention weights and eliminates Byte-Pair Encoding (BPE) bloat.\n* **Flags:** [`ENABLE_SYNTHETIC_SWAPPING`](DEPLOYMENT.md)'
)

# Fix Tier 3 header
content = content.replace(
    '### Tier 3: Script-Aware Non-Latin & CJK Rehydration Engine',
    '### Tier 3: Quantized ONNX BERT-NER (Conversational Entities)\n* **Implementation Mechanics:** Quantized ONNX BERT-NER models execute natively in-memory (optional NLP mode) for context-aware entity extraction. Supports BYOM (Bring Your Own Model) for specialized architectures like BioBERT, ClinicalBERT, XLM-RoBERTa, and Legal-BERT.\n\n### Step 3 of Masking: Script-Aware Non-Latin & CJK Rehydration Engine'
)

# Also need to remove the old lines that I replaced manually in the replace block above
# Let me write a more robust replacement


def rewrite_architecture(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Let's just do targeted string replaces for the bad headers
    content = content.replace('### Tier 2: Shannon Entropy & Format-Preserving Synthetic Masking', '### Tier 2: Shannon Entropy\n\n### Step 2: Format-Preserving Synthetic Masking')
    content = content.replace('### Tier 3: Script-Aware Non-Latin & CJK Rehydration Engine', '### Step 3: Script-Aware Non-Latin & CJK Rehydration Engine')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

rewrite_architecture('ARCHITECTURE.md')
print("ARCHITECTURE.md rewritten")
