from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import RELEASED_CONFIGURATION_ID, CatalogError, _validate_configuration


class ReleasedConfigError(ValueError):
    """A released-product configuration cannot be rendered from reviewed inputs."""


@dataclass(frozen=True)
class RenderedConfig:
    path: Path
    environment: dict[str, str]
    template_sha256: str
    rendered_sha256: str


def _synthetic_key(value: str) -> str:
    if (
        not isinstance(value, str) or not 8 <= len(value) <= 256
        or not value.isascii() or not all(character.isalnum() or character in "-_." for character in value)
    ):
        raise ReleasedConfigError("synthetic key is invalid")
    return value


def _capture_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ReleasedConfigError("capture route is invalid") from exc
    if (
        parsed.scheme != "http" or parsed.hostname != "host.docker.internal"
        or parsed.username is not None or parsed.password is not None
        or not port or not 0 < port < 65536
        or parsed.path or parsed.query or parsed.fragment
        or parsed.netloc != f"host.docker.internal:{port}"
    ):
        raise ReleasedConfigError("capture route is not the run-owned Docker host bridge")
    return value


def render_released_config(
    template_path: Path,
    output_path: Path,
    *,
    capture_base_url: str,
    upstream_key: str,
    virtual_key: str,
) -> RenderedConfig:
    """Render only the reviewed response-on template with a bound capture and synthetic keys."""
    capture_url = _capture_url(capture_base_url)
    substitutions = {
        "CAPTURE_BASE_URL": capture_url,
        "SYNTHETIC_UPSTREAM_KEY": _synthetic_key(upstream_key),
        "SYNTHETIC_VIRTUAL_KEY": _synthetic_key(virtual_key),
    }
    try:
        template, template_sha256 = _validate_configuration(
            template_path, configuration_id=RELEASED_CONFIGURATION_ID,
        )
    except CatalogError as exc:
        raise ReleasedConfigError("reviewed configuration template is invalid") from exc
    environment = {
        key: substitutions[value[2:-2]] if value.startswith("{{") else value
        for key, value in template["environment"].items()
    }
    document = {**template, "environment": environment}
    payload = (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not output_path.parent.is_dir() or output_path.parent.is_symlink():
        raise ReleasedConfigError("rendered configuration destination is unavailable")
    try:
        descriptor = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_BINARY if os.name == "nt" else os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        raise ReleasedConfigError("rendered configuration cannot be created") from exc
    return RenderedConfig(
        path=output_path,
        environment=environment,
        template_sha256=template_sha256,
        rendered_sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
    )
