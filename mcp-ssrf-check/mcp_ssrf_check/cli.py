"""``mcp-ssrf-check``: point it at your own MCP server and read the table."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from . import __version__
from .checks import (
    check_baseline,
    check_host_header,
    check_null_origin,
    check_origin_header,
    check_session_binding,
    check_tool_url_ssrf,
)
from .listener import CallbackListener
from .report import EXIT_INCONCLUSIVE, INCONCLUSIVE, SKIP, CheckResult, Report
from .transport import TargetUnreachable, open_lifecycle

CHECK_NAMES = ("host", "origin", "origin-null", "session", "ssrf")

# Read when --bearer is absent, so CI can pass a secret without putting it in argv, where
# every other process on the runner can read it.
BEARER_ENV = "MCP_SSRF_CHECK_BEARER"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-ssrf-check",
        description=(
            "Run Host, Origin, session and URL-fetching SSRF checks against an MCP server you operate. "
            "It sends requests only to that server and to a loopback listener it opens itself."
        ),
    )
    parser.add_argument("--url", required=True, help="the server's MCP endpoint, for example http://127.0.0.1:8000/mcp")
    parser.add_argument("--lifecycle", choices=("auto", "stateless", "stateful"), default="auto")
    parser.add_argument(
        "--bearer", help=f"bearer token sent as Authorization on every request (default: ${BEARER_ENV}, if set)"
    )
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE", help="extra header, repeatable")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    parser.add_argument("--skip", default="", help="comma-separated checks to skip: " + ", ".join(CHECK_NAMES))
    parser.add_argument("--fetch-tool", help="name of a tool that fetches a URL; enables the SSRF check")
    parser.add_argument("--url-argument", default="url", help="the tool argument that carries the URL (default: url)")
    parser.add_argument("--control-url", help="a URL the tool is expected to fetch successfully, to prove the wiring")
    parser.add_argument("--listen-host", default="127.0.0.1", help="address the callback listener binds (default loopback)")
    parser.add_argument("--listen-port", type=int, default=0, help="callback listener port (default: any free port)")
    parser.add_argument("--callback-host", default="127.0.0.1", help="host the server should use to reach the listener")
    parser.add_argument(
        "--redirect-target",
        help=(
            "enable the redirect probe: an address the listener answers on that the server should refuse when "
            "asked directly (for example 127.0.0.1 when server and checker share a host)"
        ),
    )
    parser.add_argument("--settle", type=float, default=0.5, help="seconds to wait for a callback after each tool call")
    parser.add_argument("--no-ipv6", action="store_true", help="do not bind the listener on ::1")
    parser.add_argument("--json-out", help="write the report as JSON to this path")
    parser.add_argument("--markdown-out", help="write the result table as Markdown to this path (for a CI job summary)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _parse_headers(pairs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--header expects NAME=VALUE, got {pair!r}")
        out[name.strip()] = value.strip()
    return out


def run(args: argparse.Namespace) -> Report:
    skipped = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = skipped - set(CHECK_NAMES)
    if unknown:
        raise SystemExit(f"--skip names unknown checks: {', '.join(sorted(unknown))}")

    headers = _parse_headers(args.header)
    bearer = args.bearer or os.environ.get(BEARER_ENV)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    checks: List[CheckResult] = []
    lifecycle = None
    with httpx.Client(timeout=args.timeout, headers=headers, follow_redirects=False) as client:
        try:
            lifecycle = open_lifecycle(client, args.url, None if args.lifecycle == "auto" else args.lifecycle)
        except TargetUnreachable as exc:
            checks.append(CheckResult("baseline", "Legitimate request accepted", INCONCLUSIVE, str(exc), {}))
            return Report(args.url, None, None, checks, __version__, generated_at)

        baseline = check_baseline(client, args.url, lifecycle)
        checks.append(baseline)
        if baseline.status == INCONCLUSIVE:
            return Report(args.url, lifecycle.name, lifecycle.version, checks, __version__, generated_at)

        if "host" not in skipped:
            checks.append(check_host_header(client, args.url, lifecycle))
        if "origin" not in skipped:
            checks.append(check_origin_header(client, args.url, lifecycle))
        if "origin-null" not in skipped:
            checks.append(check_null_origin(client, args.url, lifecycle))
        if "session" not in skipped:
            checks.append(check_session_binding(client, args.url, lifecycle))

        if "ssrf" in skipped or not args.fetch_tool:
            checks.append(
                CheckResult(
                    "tool-url-ssrf",
                    "URL-fetching tool refuses loopback",
                    SKIP,
                    "not requested: pass --fetch-tool NAME (and --url-argument) to run it",
                    {},
                )
            )
        else:
            with CallbackListener(args.listen_host, args.listen_port, ipv6=not args.no_ipv6, redirect_host=args.redirect_target) as listener:
                checks.extend(
                    check_tool_url_ssrf(
                        client,
                        args.url,
                        lifecycle,
                        tool=args.fetch_tool,
                        argument=args.url_argument,
                        listener=listener,
                        callback_host=args.callback_host,
                        redirect_target=args.redirect_target,
                        control_url=args.control_url,
                        settle=args.settle,
                    )
                )
    return Report(args.url, lifecycle.name, lifecycle.version, checks, __version__, generated_at)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(args)
    except httpx.HTTPError as exc:
        print(f"mcp-ssrf-check: {type(exc).__name__} while talking to {args.url}", file=sys.stderr)
        return EXIT_INCONCLUSIVE
    print(report.render_text())
    if args.json_out:
        report.write_json(args.json_out)
        print(f"report written to {args.json_out}")
    if args.markdown_out:
        report.write_markdown(args.markdown_out)
    return report.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
