"""Call the scoped /v1/mcp JSON-RPC gateway; this is not a full MCP transport client."""

import os

import httpx

gateway = os.getenv("SHIELD_MCP_URL", "http://localhost:8000/v1/mcp")
headers = {
    "X-Shield-Virtual-Key": os.getenv("SHIELD_CLIENT_KEY", "evaluation-key"),
}
if upstream := os.getenv("SHIELD_MCP_UPSTREAM_URL"):
    headers["X-Shield-Upstream-URL"] = upstream

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {},
}

response = httpx.post(gateway, headers=headers, json=payload, timeout=30.0)
response.raise_for_status()
print(response.json())
