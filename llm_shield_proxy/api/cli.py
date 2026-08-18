"""CLI Entrypoint for LLM-Shield-Proxy."""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == 'win32':
    asyncio.WindowsSelectorEventLoopPolicy = asyncio.WindowsProactorEventLoopPolicy

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

    args = parser.parse_args()

    uvicorn.run(
        "llm_shield_proxy.api.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
