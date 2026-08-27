"""CLI Entrypoint for LLM-Shield-Proxy."""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # type: ignore

import uvicorn

from llm_shield_proxy.core.config import settings


def main() -> None:
    """Parses command-line arguments and launches the Uvicorn server."""
    parser = argparse.ArgumentParser(
        prog="llm-shield-proxy",
        description="LLM-Shield-Proxy: Enterprise Zero-Egress Privacy Redaction Proxy",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=settings.HOST,
        help=f"Socket host to bind (default: {settings.HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.PORT,
        help=f"Socket port to bind (default: {settings.PORT})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=settings.WORKERS,
        help=f"Number of worker processes (default: {settings.WORKERS})",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=settings.LOG_LEVEL.lower(),
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help=f"Logging level (default: {settings.LOG_LEVEL.lower()})",
    )
    parser.add_argument(
        "--tls-cert-file",
        type=str,
        default=settings.TLS_CERT_FILE,
        help="Path to server public certificate for inbound TLS",
    )
    parser.add_argument(
        "--tls-key-file",
        type=str,
        default=settings.TLS_KEY_FILE,
        help="Path to server private key for inbound TLS",
    )
    parser.add_argument(
        "--client-ca-file",
        type=str,
        default=settings.CLIENT_CA_FILE,
        help="Path to CA bundle to verify inbound mTLS clients",
    )
    parser.add_argument(
        "--ca-bundle-file",
        type=str,
        default=settings.CA_BUNDLE_FILE,
        help="Path to custom CA bundle for outbound upstream verification",
    )
    parser.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        default=settings.INSECURE_SKIP_VERIFY,
        help="Bypass outbound upstream certificate validation",
    )

    args = parser.parse_args()

    kwargs = {
        "host": args.host,
        "port": args.port,
        "workers": args.workers,
        "reload": args.reload,
        "log_level": args.log_level,
    }

    if args.tls_cert_file and args.tls_key_file:
        kwargs["ssl_certfile"] = args.tls_cert_file
        kwargs["ssl_keyfile"] = args.tls_key_file
        if args.client_ca_file:
            kwargs["ssl_ca_certs"] = args.client_ca_file
            import ssl
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED

    uvicorn.run("llm_shield_proxy.api.main:app", **kwargs)


if __name__ == "__main__":
    main()
