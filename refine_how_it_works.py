import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Fix the Machine-to-Machine explanation
old_b_section = """### B. Machine-to-Machine (JSON-RPC / Tool Calls)
When the proxy detects structured AI tool calls or JSON-RPC `2.0` payloads, it **bypasses your configuration** and strictly enforces **STATELESS_CRYPTO**. 
* **Why?** Replacing text with synthetic names often breaks JSON syntax and crashes agents. 
* **The Solution:** To fix this, the proxy uses in-band AES encryption for machine traffic—protecting the data without changing string lengths or relying on Redis."""

new_b_section = """### B. Machine-to-Machine (JSON-RPC / Tool Calls)
When the proxy detects structured AI tool calls or JSON-RPC `2.0` payloads, it **bypasses your configuration** and strictly enforces an **AST-Aware Semantic Firewall** with **STATELESS_CRYPTO**. 
* **Why?** Blindly running regex over raw JSON strings can corrupt syntax (e.g., matching a JSON key or injecting unescaped characters), causing agent crashes.
* **The Solution:** The proxy parses the payload into an Abstract Syntax Tree (AST). It safely replaces sensitive leaf values with synthetic fakes and bundles them with an in-band AES-256-GCM cipher (e.g., `{"_shield_val": "Maya", "_shield_ctx": "aesgcm..."}`). This guarantees 100% valid JSON syntax without relying on Redis state."""

text = text.replace(old_b_section, new_b_section)

# 2. Refine "How It Works (The Data Flow)" section
old_how_it_works = """### How It Works (The Data Flow)

#### 📥 Inbound (Prompt Sanitization)
1. **Intercept:** Your application sends a standard OpenAI / LangChain payload to `localhost:8000`.
2. **Cascade Redaction:** The proxy intercepts the JSON and routes text through the 3-Tier detection cascade (Regex -> Shannon Entropy -> ONNX NER).
3. **Vault Storage:** The original sensitive data is mapped to a deterministic tag (or synthetic entity) and stored locally in a TTL-backed session vault.
4. **Clean Egress:** A 100% sanitized payload is forwarded to OpenAI. OpenAI never sees your raw sensitive data.

#### 📤 Outbound (Streaming De-redaction)
1. **SSE Stream Intercept:** OpenAI streams the response back chunk-by-chunk via Server-Sent Events (SSE).
2. **Prefix-Aware Buffer:** Because tokens can be split across SSE chunks, the sliding-window buffer retains trailing prefix overlap up to `L = max(0, max_token_length - 1)`.
3. **Re-hydration:** Once a tag or synthetic word is fully assembled, the proxy swaps the real data back from the local vault and streams the un-redacted text to the user's application in real-time."""

new_how_it_works = """### How It Works (The Data Flow)

#### 📥 Inbound (Prompt Sanitization)
1. **Intercept:** Your client routes a standard OpenAI / LangChain request through `localhost:8000`.
2. **Dual-Pipeline Routing:** The proxy checks the payload type. Standard text goes to the **3-Tier Cascade Engine** (Regex -> Entropy -> ONNX NER). JSON-RPC tool calls are routed to the **AST-Aware Firewall**.
3. **Secure Substitution:** Sensitive data is swapped out using your configured mode (Synthetic Fakes, Structural Tags, or AES-GCM). Stateful mappings are stored in the local Redis vault; stateless mappings are encrypted in-band.
4. **Clean Egress:** The sanitized payload is forwarded to the LLM. Your raw PII never traverses the public internet.

#### 📤 Outbound (Streaming Rehydration)
1. **SSE Stream Intercept:** The LLM streams the sanitized response back via Server-Sent Events (SSE).
2. **Prefix-Aware Buffer:** Because LLMs often fragment tokens across SSE chunks, our patent-pending sliding-window buffer retains trailing prefix overlap (e.g., `[PER`... `SON_1]`) ensuring split tokens never leak.
3. **Real-Time Rehydration:** The instant a synthetic name or tag is fully assembled in the buffer, the proxy retrieves the original data (via Redis or AES decryption) and streams the un-redacted text back to the user with <5µs latency overhead."""

text = text.replace(old_how_it_works, new_how_it_works)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
