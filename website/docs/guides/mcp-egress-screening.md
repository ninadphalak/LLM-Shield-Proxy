# MCP Egress Screening: A Worked Example

[⬅️ Back to MCP Tool Policy Configuration](/docs/guides/mcp-tool-governance) · [SSRF & DNS-Rebinding Egress Firewall](/docs/features/secure-infrastructure-service-mesh/ssrf-dns-rebinding-egress-firewall)

This page walks through how LLM-Shield-Proxy screens outbound destinations on its `POST /v1/mcp` route, one property at a time, with the test that holds each property. It is written for people building or reviewing an MCP server, gateway or agent runtime that fetches URLs on the model's behalf. The code is small enough to read in one sitting: [`security/egress_guard.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/llm_shield_proxy/security/egress_guard.py) (about 400 lines) and the egress parts of [`api/mcp_router.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/llm_shield_proxy/api/mcp_router.py).

**Scope.** The route is a JSON-RPC gateway for `tools/call`, `tools/list` and `resources/read` inside a controlled network boundary. It does not implement the Streamable HTTP lifecycle (no `initialize`, no sessions, no GET stream), so `Host` and `Origin` validation belong to whatever listens in front of it, not to this code. See [STABILITY.md](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/STABILITY.md) for the maturity tier.

## The problem in one paragraph

A tool argument is text the model wrote. The model wrote it from whatever it was given: a page, a document, an earlier tool result. When a tool takes a URL and fetches it, the fetch is a request the server makes to a destination an untrusted party chose. Cloud metadata endpoints, loopback services and private ranges are the usual targets. A gateway that forwards `tools/call` has the same problem twice: once for URLs inside the request, and once for the upstream server address, which a caller supplies by header or an operator by environment variable.

## The properties, each with its test

### 1. Authorise before you resolve

Resolving a hostname is work done on behalf of the caller. If the egress scan ran before the tool authorisation check, a caller whose role forbids every tool could still make the gateway resolve any name it put in `params`: an outbound DNS probe for someone not allowed to call anything. The RBAC gate therefore runs first, and a forbidden tool is rejected with `-32003` before any resolver is touched.

Test: `test_a_forbidden_tool_is_rejected_before_any_dns_resolution` in [`tests/test_mcp_routing.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_mcp_routing.py).

### 2. Scan all of `params`, on every method

`resources/read` exists to fetch a URI. A URL in `_meta` reaches an upstream tool as readily as one in `arguments`. The scan therefore walks the whole `params` object for every supported method, to a bounded depth, and collects every `http(s)://` substring it finds. `find_urls` in `egress_guard.py` is the walker.

Tests: `test_tools_call_ssrf_loopback_ip_nested_in_arguments_blocked`, `test_tools_call_ssrf_metadata_ip_blocked_and_does_not_route_upstream`.

### 3. Every record, not the first

A resolver that answers with one public address and one `169.254.169.254` is rejected on the strength of the second record alone. `evaluate_url` iterates every A and AAAA record.

Test: `test_dns_rebinding_second_record_blocked` in [`tests/test_egress_guard.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_egress_guard.py).

### 4. Resolution failure is a refusal

A timeout, an NXDOMAIN, an empty answer or a resolver exception all raise `EgressPolicyViolationError`. There is no path that lets an unresolved name through.

Tests: `test_dns_resolution_failure_fails_closed`, `test_dns_timeout_fails_closed`, `test_empty_dns_answer_fails_closed`.

### 5. Parse addresses, do not match strings

`::ffff:169.254.169.254` is the metadata address written as an IPv4-mapped IPv6 literal. `_normalize` unwraps it before the CIDR match. Literal hosts go through `ipaddress.ip_address`, not a regular expression. The baseline denylist (RFC 1918, loopback, link-local, CGNAT, the IETF special-purpose ranges and their IPv6 equivalents) applies in every mode and cannot be overridden; `additional_denied_cidrs` only adds to it. A policy whose `egress_mode` is misspelled falls to `ALLOWLIST_ONLY`, the stricter mode, rather than to the permissive default.

Tests: `test_cloud_metadata_ip_blocked`, `test_compile_policy_rejects_unknown_mode_by_failing_closed`.

### 6. Check and pin in one step

Validating a hostname and then handing the same hostname to an HTTP client leaves a window: the client resolves the name a second time at connect, and a low-TTL zone can answer public to the check and `169.254.169.254` to the connection. `resolve_pinned_target` returns the validated address already substituted into the URL, with the original `host:port` carried in the `Host` header and the hostname in the TLS `sni_hostname` extension, so virtual hosting and certificate verification still see the real name. Callers that dispatch a request use it; `evaluate_url` alone is for callers that only need a verdict.

Tests: `test_resolve_pinned_target_closes_the_rebinding_window_between_check_and_connect`, `test_pin_url_to_ip_sets_sni_hostname_so_tls_verification_still_uses_the_real_name`, `test_upstream_hostname_is_dialed_by_pinned_ip_with_hostname_preserved`.

### 7. Re-check what a transformation introduced

The gateway redacts tool arguments before forwarding them, and the redaction substitutes realistic look-alike values so that upstream schemas keep validating. When personal data sits inside a URL's authority, that substitution rewrites the host:

```text
checked    https://bob@example.com.attacker.example/x
forwarded  https://jacksondaniel@example.net/x
```

A different registrable domain, never resolved, never evaluated, handed to the upstream tool to dial. The fix is a second pass over the difference between the URLs found before and after redaction; a payload the redaction did not rewrite costs no extra resolution.

Tests: `test_a_rewritten_host_that_is_forbidden_is_blocked`, `test_every_forwarded_url_was_actually_checked`, `test_an_unrewritten_payload_costs_no_extra_resolution`.

### 8. The upstream target is input too

`X-Shield-Upstream-URL` and `UPSTREAM_MCP_BASE_URL` name the server the gateway will dial. They clear the same firewall as URLs inside the request, once per gateway call, and the dial goes to the pinned address.

Test: `test_upstream_hostname_resolving_to_metadata_ip_is_blocked_before_any_dispatch`.

### 9. The audit record names the host, not the URL

A blocked URL is attacker-shaped and identifiers sit in paths as readily as in query strings. The signed audit chain records scheme and authority only, plus the resolved address and the matched rule as their own fields. The reduction runs inside the violation handler, so it fails soft: a URL that cannot be parsed is recorded as unparseable rather than allowed to turn a refusal into a 500.

Tests: `test_tool_argument_url_secret_is_redacted_in_audit`, `test_redact_url_for_audit_drops_identifier_bearing_paths`, `test_redact_url_for_audit_survives_a_malformed_port`.

### 10. One resolution per URL per call

The first version of the router scanned `params` and then `params["arguments"]` again so the audit record could name the tool. That doubled outbound DNS on every allowed call. The tool name is now in the record and each URL is resolved once.

Test: `test_an_allowed_call_resolves_each_url_once`.

## What this code does not do

- **It cannot pin for the tool server.** The gateway refuses arguments whose destinations resolve badly at check time. The tool server then dials the URL itself, with its own resolver. If that server has no guard of its own, a name that flips after the gateway's check still reaches it. Screening at an intermediary reduces exposure; it does not replace the same controls inside the fetcher.
- **It does not follow redirects.** The gateway never fetches a tool argument, so it never sees a redirect. A tool server that follows redirects needs to validate each hop.
- **It does not validate `Host` or `Origin` on its own listener.** Those checks belong to the HTTP front door of a Streamable HTTP server; this route is not one.

## Checking your own server

[`mcp-ssrf-check`](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/mcp-ssrf-check) is a small tool that runs the client side of these properties against an MCP server you operate: `Host` and `Origin` validation, session id binding, and, for a tool you name, whether it can be made to fetch loopback under nine spellings of the address, and, when you name a redirect target, whether it follows a redirect into it. It sends requests only to your server and to a listener it opens on your own machine.

```bash
pip install mcp-ssrf-check
mcp-ssrf-check --url http://127.0.0.1:8000/mcp --fetch-tool fetch --url-argument url
```

## Related reading

- [MCP Tool Policy Configuration](/docs/guides/mcp-tool-governance): the policy schema and client recipes for the route.
- [SSRF & DNS-Rebinding Egress Firewall](/docs/features/secure-infrastructure-service-mesh/ssrf-dns-rebinding-egress-firewall): the feature summary.
- MCP security best practices, [Server-Side Request Forgery](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices#server-side-request-forgery-ssrf): the specification's guidance for clients fetching OAuth metadata, which the properties above apply to the server side.
