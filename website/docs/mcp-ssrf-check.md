---
slug: /mcp-ssrf-check
title: mcp-ssrf-check
sidebar_label: Overview
sidebar_position: 1
description: mcp-ssrf-check tests an MCP server you operate for missing Host and Origin validation, session ids it never issued, and URL-fetching tools that can be made to fetch loopback. One command, or a GitHub Action.
keywords: [MCP security, SSRF, DNS rebinding, Host header validation, Origin validation, Model Context Protocol, GitHub Action]
---

# mcp-ssrf-check

`mcp-ssrf-check` tests an MCP server you operate. It tells you whether the server validates
the `Host` and `Origin` headers, whether it honours session ids it never issued, and, if you
name a tool that fetches URLs, whether that tool can be made to fetch loopback.

It sends requests to two places only: the server you name, and a listener it opens on your
own machine. It does not scan anything else, and it does not read or store tool output.

```bash
pip install mcp-ssrf-check
mcp-ssrf-check --url http://127.0.0.1:8000/mcp
mcp-ssrf-check --url http://127.0.0.1:8000/mcp --fetch-tool fetch --url-argument url
```

Exit code `0` means no check failed, `1` means at least one failed, and `2` means the result
is inconclusive (for example, the server could not be reached as a normal client).

## What it checks

| Check | Passes when |
| :--- | :--- |
| `host-header` | A request for an unknown `Host` is refused with a 4xx |
| `origin-header` | A request from an unknown `Origin` is refused |
| `session-binding` | A made-up session id and a deleted one are both refused |
| `tool-url-ssrf` | The named tool refuses a loopback URL in all nine spellings (`127.0.0.1`, `localhost`, `[::1]`, `2130706433`, `0x7f000001` and others), and nothing reaches the listener |

A guard that compares host strings passes the first spelling and fails the rest. A guard
that resolves the name and checks every address passes them all.

## In CI

```yaml
- name: Check Host, Origin, sessions and URL-fetching SSRF
  uses: ninadphalak/LLM-Shield-Proxy/mcp-ssrf-check@mcp-check-v0.2.0
  with:
    url: http://127.0.0.1:8000/mcp
    fetch-tool: fetch
    url-argument: url
```

The Action writes the result table to the job summary, uploads the JSON report and fails the
step when a check fails. Start your server in an earlier step.

## What it does not check

DNS rebinding with a changing DNS answer, private ranges other than loopback, and
`resources/read`. Every option, input and output is in the
[full README](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/mcp-ssrf-check).

For how LLM-Shield-Proxy itself screens outbound URLs on its MCP route, see
[MCP egress screening](guides/mcp-egress-screening.md).
