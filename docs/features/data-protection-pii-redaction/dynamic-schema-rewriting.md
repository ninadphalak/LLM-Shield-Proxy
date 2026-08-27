# Dynamic Schema Rewriting

[⬅️ Back to Features Catalog](../../../FEATURES.md)

## What It Does
**Dynamic Schema Rewriting** is the core mechanism that allows LLM-Shield-Proxy to guarantee zero-data leakage in Machine-to-Machine traffic (JSON-RPC / MCP tools) *without* requiring an external state store like Redis.

By dynamically intercepting and rewriting the function schemas (e.g., OpenAI Tool Definitions or MCP Schemas) sent to the LLM, the proxy legally binds the LLM to return cryptographic state information.

## How It Works

When an AI agent's traffic passes through the proxy, the AST Firewall extracts sensitive PII using **Shannon Entropy** and standard Regex rules. The proxy then replaces the PII with a synthetic `canonical locale` substitute (e.g. swapping a real SSN for a fake SSN) to preserve JSON structure and LLM attention weights. Simultaneously, it generates a stateless AES-256-GCM encrypted envelope (the ciphertext) of the original string.

However, if the proxy just sends this synthetic data to the LLM, the LLM will never send the encrypted context back, breaking the session when the tool returns. 

To solve this, the **DynamicSchemaRewriter** steps in:

1. **Schema Interception:** The proxy intercepts the JSON Schema describing the tool (e.g., `fetch_profile(ssn: string)`).
2. **Sibling Injection:** For any string property that might contain redacted PII, the proxy dynamically injects a sibling property named `_ctx_hash_<prop_name>`.
3. **The "Force Echo" Required Array:** The proxy appends `_ctx_hash_<prop_name>` into the JSON Schema's `"required"` array.

### Before Rewriting (Original Schema)
```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {
      "type": "string",
      "description": "The customer's social security number."
    }
  },
  "required": ["customer_ssn"]
}
```

### After Rewriting (Intercepted Schema sent to LLM)
```json
{
  "type": "object",
  "properties": {
    "customer_ssn": {
      "type": "string",
      "description": "The customer's social security number."
    },
    "_ctx_hash_customer_ssn": {
      "type": "string",
      "description": "Cryptographic context for customer_ssn. Must be provided if customer_ssn is redacted."
    }
  },
  "required": [
    "customer_ssn",
    "_ctx_hash_customer_ssn"
  ]
}
```

## Why This is Powerful (Mathematical Echoing)
Because the `_ctx_hash` field is strictly defined in the `"required"` array, the downstream LLM (like GPT-4, Claude, or Gemini) is constrained by its own output parsers to always include it when calling the tool.

### Example: The Intercepted JSON-RPC Payload
When the LLM eventually decides to call the `fetch_profile` tool, the proxy receives a payload that looks exactly like this:

```json
{
  "jsonrpc": "2.0",
  "method": "fetch_profile",
  "params": {
    "customer_ssn": "555-01-9999", // The LLM generates a synthetic canonical locale SSN
    "_ctx_hash_customer_ssn": "enc_v1_GCM_a7f9b2c3d4e5f6...8d4c" // The secure AES-256-GCM cipher-text is safely echoed back!
  },
  "id": 1
}
```

When the LLM calls the tool, it echoes back the AES-256-GCM cipher-text. The proxy intercepts the tool call, decrypts the `_ctx_hash` (`enc_v1_GCM_a7f9...`), and restores the original PII *instantly* with zero database lookups, granting infinite horizontal scalability.

> **Visual Demonstration:** 
> ![Stateless Rehydration](../../../LLM-Shield-Proxy-paper-v2.gif)
