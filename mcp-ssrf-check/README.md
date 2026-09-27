# mcp-ssrf-check

Point it at an MCP server you operate. It tells you whether that server validates the `Host`
and `Origin` headers, whether it honours session ids it never issued, and, if you name a
tool that fetches URLs, whether that tool can be made to fetch loopback.

It sends requests to two places only: the server you name, and a listener it opens itself
on your own machine. It never scans anything else, and it does not read or store tool output.

```bash
pip install "git+https://github.com/ninadphalak/LLM-Shield-Proxy#subdirectory=mcp-ssrf-check"
mcp-ssrf-check --url http://127.0.0.1:8000/mcp
mcp-ssrf-check --url http://127.0.0.1:8000/mcp --fetch-tool fetch --url-argument url
```

Not on PyPI yet. From a clone, `pip install ./mcp-ssrf-check` does the same.

## What it checks

| Check | What is sent | Pass | Fail |
|---|---|---|---|
| `baseline` | The lifecycle's opening request, unmodified | 2xx with a JSON-RPC result | Anything else. The run stops: the other checks cannot be read. |
| `host-header` | The same request with `Host: rebound-<nonce>.invalid` | Any 4xx | Served. A DNS-rebound page reaches this server. |
| `origin-header` | The same request with `Origin: https://evil-<nonce>.invalid` | 403 (any 4xx is recorded as a pass, with the status) | Served. The transport specification requires 403. |
| `origin-null` | `Origin: null` | Reported, never scored | |
| `session-binding` | `tools/list` with a fabricated `Mcp-Session-Id`; then a fresh session, DELETE, and reuse | Both refused (404) | Either honoured |
| `tool-url-ssrf` | `tools/call` on the named tool with a URL for the checker's own loopback listener, spelled nine ways, plus a redirect | Every spelling refused and nothing reached the listener | Anything reached the listener, or a redirect was followed to loopback |

The loopback spellings are `127.0.0.1`, `localhost`, `[::1]`, `[::ffff:127.0.0.1]`,
`2130706433`, `0x7f000001`, `0177.0.0.1`, `127.1` and `0.0.0.0`. A guard that compares host
strings passes the first and fails the rest; a guard that resolves and then checks every
address passes them all. The report names each spelling that arrived.

`session-binding` is skipped on the stateless lifecycle (protocol `2026-07-28` and later),
which has no sessions, and on stateful servers that issue no session id.

## Reading the result

Exit code 0 means no check failed. Exit code 1 means at least one `FAIL`. Exit code 2 means
`INCONCLUSIVE`: the server could not be reached as a legitimate client, the tool or argument
name was wrong (`-32602`), or the tool answered without an error while nothing reached the
listener. That last case usually means the server runs in a different network namespace
from the checker (a container, another host), so its loopback is not yours: run the checker
where the server runs, or pass `--listen-host` and `--callback-host` with an address the
server can reach.

`--control-url` names a URL the tool is expected to fetch successfully. It proves the tool
and argument are wired before the loopback probes are read.

Evidence in the report is HTTP status codes, JSON-RPC message shapes (`result` or `error`
plus the error code), the spellings sent, and which peer address arrived at the listener.
No response body is copied into it.

## What it does not check

- DNS rebinding proper, where a name changes its answer between check and connect. That
  needs a DNS zone you control; this tool has none.
- Private-range egress to addresses other than loopback. The probes target only the
  checker's own listener.
- `resources/read` with a URI the server fetches. Only `tools/call` is exercised.
- Anything the official conformance suite already covers. Its `dns-rebinding` scenario sends
  `Host` and `Origin` together and accepts any 4xx; this tool reports them separately
  because the two headers defend against different things.

## In CI

```yaml
- name: Start the server under test
  run: |
    python -m my_mcp_server --port 8000 &
    sleep 2
- name: Check Host, Origin, sessions and URL-fetching SSRF
  run: |
    pip install "git+https://github.com/ninadphalak/LLM-Shield-Proxy#subdirectory=mcp-ssrf-check"
    mcp-ssrf-check --url http://127.0.0.1:8000/mcp \
      --fetch-tool fetch --url-argument url \
      --json-out mcp-ssrf-check.json
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: mcp-ssrf-check
    path: mcp-ssrf-check.json
```

## Options

```
--url URL                 the MCP endpoint (required)
--lifecycle auto|stateless|stateful
--bearer TOKEN            sent as Authorization: Bearer on every request
--header NAME=VALUE       extra header, repeatable
--timeout SECONDS         HTTP timeout (default 10)
--skip host,origin,origin-null,session,ssrf
--fetch-tool NAME         enables the SSRF check
--url-argument NAME       the tool argument that carries the URL (default: url)
--control-url URL         a URL the tool should fetch successfully
--listen-host ADDR        where the callback listener binds (default 127.0.0.1)
--listen-port PORT        default: any free port
--callback-host HOST      the address the server should use to reach the listener
--settle SECONDS          wait for a callback after each tool call (default 0.5)
--no-ipv6                 do not also bind the listener on ::1
--json-out PATH           write the report as JSON
```

## Why it exists

Two of the checks correspond to requirements in the Streamable HTTP transport specification
(Origin validation with 403; 404 for terminated sessions). The SSRF check corresponds to the
"Server-Side Request Forgery" entry in the MCP security best practices, extended to the
server side: a tool whose arguments carry a URL is a fetcher the model controls, and the
mitigations written for clients fetching OAuth metadata apply to it unchanged.

Dependencies: the standard library and `httpx`. Apache-2.0.
