# mcp-ssrf-check

Point it at an MCP server you operate. It tells you whether that server validates the `Host`
and `Origin` headers, whether it honours session ids it never issued, and, if you name a
tool that fetches URLs, whether that tool can be made to fetch loopback.

It sends requests to two places only: the server you name, and a listener it opens itself
on your own machine. It never scans anything else, and it does not read or store tool output.

```bash
pip install mcp-ssrf-check
mcp-ssrf-check --url http://127.0.0.1:8000/mcp
mcp-ssrf-check --url http://127.0.0.1:8000/mcp --fetch-tool fetch --url-argument url
```

From a clone, `pip install ./mcp-ssrf-check` installs the working tree instead.

## What it checks

| Check | What is sent | Pass | Fail |
|---|---|---|---|
| `baseline` | The lifecycle's opening request, unmodified | 2xx with a JSON-RPC result | Anything else. The run stops: the other checks cannot be read. |
| `host-header` | The same request with `Host: rebound-<nonce>.invalid` | Any 4xx | Served. A DNS-rebound page reaches this server. |
| `origin-header` | The same request with `Origin: https://evil-<nonce>.invalid` | 403 (any 4xx is recorded as a pass, with the status) | Served. The transport specification requires 403. |
| `origin-null` | `Origin: null` | Reported, never scored | |
| `session-binding` | `tools/list` with a fabricated `Mcp-Session-Id`; then a fresh session, DELETE, and reuse | Both refused (404) | Either honoured |
| `tool-url-ssrf` | `tools/call` on the named tool with a URL for the checker's own loopback listener, spelled nine ways; with `--redirect-target`, also a redirect from `--callback-host` into that target | Every spelling refused and nothing reached the listener | Anything reached the listener, or the redirect was followed into the target |

The loopback spellings are `127.0.0.1`, `localhost`, `[::1]`, `[::ffff:127.0.0.1]`,
`2130706433`, `0x7f000001`, `0177.0.0.1`, `127.1` and `0.0.0.0`. A guard that compares host
strings passes the first and fails the rest; a guard that resolves and then checks every
address passes them all. The report names each spelling that arrived.

The redirect probe is opt-in because it only shows something when the server allows the first
hop and refuses the target when asked directly; the checker verifies the second condition and
reports the probe as not exercised otherwise. On one host, `--callback-host localhost
--redirect-target 127.0.0.1` catches a guard that checks the hostname of the first hop and never
looks at where the redirect goes. Across a network namespace, name the address the server can
reach the listener on as `--callback-host`, and an address it should refuse as `--redirect-target`
(the listener must be bound where that address lands: `--listen-host 0.0.0.0`).

`session-binding` is skipped on the stateless lifecycle (protocol `2026-07-28` and later),
which has no sessions, and on stateful servers that issue no session id.

## Reading the result

Exit code 0 means no check failed. Exit code 1 means at least one `FAIL`. Exit code 2 means
`INCONCLUSIVE`: the server could not be reached as a legitimate client, the tool or argument
name was wrong (it is not in `tools/list`, or the server answers `-32602`), or the tool
answered without an error while nothing reached the
listener. That last case usually means the server runs in a different network namespace
from the checker (a container, another host), so its loopback is not yours: run the checker
where the server runs, or pass `--listen-host` and `--callback-host` with an address the
server can reach.

`--control-url` names a URL the tool is expected to fetch successfully. It proves the tool
and argument are wired before the loopback probes are read.

Evidence in the report is HTTP status codes, content types, JSON-RPC message shapes (`result`
or `error` plus the error code), the spellings sent, and which peer address arrived at the
listener. No response text is copied into it, not even from a rejection page, because the
report is meant to be uploaded as a CI artifact.

## What it does not check

- DNS rebinding proper, where a name changes its answer between check and connect. That
  needs a DNS zone you control; this tool has none.
- Private-range egress to addresses other than loopback. The probes target only the
  checker's own listener.
- Redirect following, unless you pass `--redirect-target`.
- `resources/read` with a URI the server fetches. Only `tools/call` is exercised.
- Anything the official conformance suite already covers. Its `dns-rebinding` scenario sends
  `Host` and `Origin` together and accepts any 4xx; this tool reports them separately
  because the two headers defend against different things.

## In CI

The GitHub Action installs the checker, runs it, writes the result table to the job summary,
uploads the JSON report as an artifact, and fails the step when a check fails. Start the server
in an earlier step; the action does not start it.

```yaml
- name: Start the server under test
  run: |
    python -m my_mcp_server --port 8000 &
    sleep 2
- name: Check Host, Origin, sessions and URL-fetching SSRF
  uses: ninadphalak/LLM-Shield-Proxy/mcp-ssrf-check@mcp-check-v0.2.1
  with:
    url: http://127.0.0.1:8000/mcp
    fetch-tool: fetch
    url-argument: url
    bearer: ${{ secrets.MCP_TOKEN }}
```

| Input | Default | Meaning |
|---|---|---|
| `url` | required | the MCP endpoint |
| `lifecycle` | `auto` | `auto`, `stateless` or `stateful` |
| `bearer` | empty | sent as `Authorization: Bearer`; passed through the environment, never argv |
| `fetch-tool` | empty | enables the SSRF check |
| `url-argument` | `url` | the tool argument that carries the URL |
| `redirect-target` | empty | enables the redirect probe |
| `callback-host` | `127.0.0.1` | the address the server uses to reach the checker's listener |
| `skip` | empty | comma-separated checks to skip |
| `json-out` | `mcp-ssrf-check-report.json` | report path |
| `fail-on` | `fail` | `fail`: the step fails on a `FAIL`. `inconclusive`: also on `INCONCLUSIVE` |
| `artifact-name` | `mcp-ssrf-check-report` | empty skips the upload |
| `source` | this tag's version | what pip installs; a path runs a working tree |

Under either `fail-on` setting the step fails when the baseline request was not accepted, since
then nothing was checked. Outputs: `outcome` (`pass`, `fail`, `inconclusive`), `exit-code` and
`report-path`. Tags are `mcp-check-vX.Y.Z`, and each installs checker version `X.Y.Z` from PyPI.

Without the action, the CLI does the same with `--json-out` and `--markdown-out` (append the
Markdown file to `$GITHUB_STEP_SUMMARY`). Set `MCP_SSRF_CHECK_BEARER` instead of passing
`--bearer`, so the token stays out of the process list.

## Options

```
--url URL                 the MCP endpoint (required)
--lifecycle auto|stateless|stateful
--bearer TOKEN            sent as Authorization: Bearer on every request
                          (default: $MCP_SSRF_CHECK_BEARER)
--header NAME=VALUE       extra header, repeatable
--timeout SECONDS         HTTP timeout (default 10)
--skip host,origin,origin-null,session,ssrf
--fetch-tool NAME         enables the SSRF check
--url-argument NAME       the tool argument that carries the URL (default: url)
--control-url URL         a URL the tool should fetch successfully
--listen-host ADDR        where the callback listener binds (default 127.0.0.1)
--listen-port PORT        default: any free port
--callback-host HOST      the address the server should use to reach the listener
--redirect-target ADDR    enable the redirect probe: where the first hop redirects to
--settle SECONDS          wait for a callback after each tool call (default 0.5)
--no-ipv6                 do not also bind the listener on ::1
--json-out PATH           write the report as JSON
--markdown-out PATH       write the result table as Markdown (for a CI job summary)
```

## Why it exists

Two of the checks correspond to requirements in the Streamable HTTP transport specification
(Origin validation with 403; 404 for terminated sessions). The SSRF check corresponds to the
"Server-Side Request Forgery" entry in the MCP security best practices, extended to the
server side: a tool whose arguments carry a URL is a fetcher the model controls, and the
mitigations written for clients fetching OAuth metadata apply to it unchanged.

Dependencies: the standard library and `httpx`. Apache-2.0.
