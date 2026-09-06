import logging
from typing import Optional
from urllib.parse import urlparse

from llm_shield_proxy.core.config import settings

logger = logging.getLogger(__name__)


def is_anthropic_host(hostname: Optional[str]) -> bool:
    """True only for Anthropic's own API domain.

    Exact match or a genuine subdomain. A bare ``endswith("anthropic.com")``
    would also accept ``notanthropic.com`` and ``anthropic.com.attacker.net``,
    which is how a lookalike upstream would talk the proxy into handing over an
    ``x-api-key``.
    """
    if not hostname:
        return False
    host = hostname.strip().lower().rstrip(".")
    return host == "anthropic.com" or host.endswith(".anthropic.com")


def resolve_provider(
    headers: dict,
    payload: Optional[dict] = None,
    upstream_host: Optional[str] = None,
) -> str:
    """
    Resolves the target upstream provider.

    Precedence:
    1. ``X-Shield-Provider`` header - an explicit caller override.
    2. Payload model-string inspection, but only when the request is already
       bound for an Anthropic host.
    3. ``DEFAULT_UPSTREAM_PROVIDER`` from config.

    Step 2 is gated on the destination on purpose. The model name says which
    model you want, never which vendor's API is about to receive it, because
    Claude is resold under a namespaced ID almost everywhere it is sold:
    ``anthropic/claude-sonnet-4.5`` (OpenRouter), ``claude-sonnet-4-5@20250929``
    (Vertex AI), ``anthropic.claude-3-5-sonnet-...`` (Bedrock), and any Azure
    deployment a customer happens to name "claude". Reading the destination out
    of the model name sent every one of those to ``api.anthropic.com`` carrying
    whichever credential belonged to the configured upstream.

    The documented contract was always the URL - "the adapter engages
    automatically when the proxy detects an Anthropic target URL" - so the
    hostname decides, and the model name only disambiguates once the request is
    already pointed at Anthropic.

    ``upstream_host`` is the *effective* upstream hostname for this request.
    Callers inside the proxy pass it explicitly, since air-gapped mode and the
    client upstream override can both retarget a request away from
    ``UPSTREAM_BASE_URL``. When omitted it falls back to the configured value.
    """
    header_provider = headers.get("x-shield-provider") or headers.get("X-Shield-Provider")
    if header_provider:
        return header_provider.lower()

    if upstream_host is None:
        upstream_host = urlparse(settings.UPSTREAM_BASE_URL).hostname

    if is_anthropic_host(upstream_host) and payload and isinstance(payload, dict):
        model = payload.get("model", "")
        if isinstance(model, str):
            if "claude" in model.lower() or model.lower().startswith("anthropic/"):
                return "anthropic"

    return settings.DEFAULT_UPSTREAM_PROVIDER.lower()
