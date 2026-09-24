from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.product_reproduction.released_config import (
    ReleasedConfigError,
    render_released_config,
)

TEMPLATE = Path("benchmarks/product_reproduction/configs/llm-shield-proxy-response-on-v1.json")


def test_rendered_configuration_uses_only_reviewed_fields(tmp_path: Path) -> None:
    rendered = render_released_config(
        TEMPLATE,
        tmp_path / "rendered.json",
        capture_base_url="http://host.docker.internal:32788",
        upstream_key="synthetic-upstream-key",
        virtual_key="synthetic-virtual-key",
    )
    assert rendered.environment["UPSTREAM_BASE_URL"] == "http://host.docker.internal:32788"
    assert rendered.environment["ENABLE_RESPONSE_PII_REDACTION"] == "true"
    assert rendered.environment["UPSTREAM_API_KEY"] == "synthetic-upstream-key"
    assert rendered.environment["VALID_VIRTUAL_KEYS"] == "synthetic-virtual-key"
    assert rendered.template_sha256.startswith("sha256:")
    assert rendered.rendered_sha256.startswith("sha256:")
    assert json.loads(rendered.path.read_text(encoding="utf-8"))["environment"] == rendered.environment


@pytest.mark.parametrize("capture_url", [
    "https://host.docker.internal:32788",
    "http://unreviewed.example:32788",
    "http://host.docker.internal:32788/unexpected",
    "http://host.docker.internal:0",
])
def test_rendered_configuration_rejects_unreviewed_capture_route(
    tmp_path: Path, capture_url: str,
) -> None:
    with pytest.raises(ReleasedConfigError):
        render_released_config(
            TEMPLATE, tmp_path / "rendered.json", capture_base_url=capture_url,
            upstream_key="synthetic-upstream-key", virtual_key="synthetic-virtual-key",
        )
    assert not (tmp_path / "rendered.json").exists()


def test_rendered_configuration_rejects_modified_template(tmp_path: Path) -> None:
    modified = tmp_path / "modified.json"
    modified.write_text(TEMPLATE.read_text(encoding="utf-8").replace(
        '"ENABLE_RESPONSE_PII_REDACTION": "true"',
        '"ENABLE_RESPONSE_PII_REDACTION": "false"',
    ), encoding="utf-8")
    with pytest.raises(ReleasedConfigError):
        render_released_config(
            modified, tmp_path / "rendered.json",
            capture_base_url="http://host.docker.internal:32788",
            upstream_key="synthetic-upstream-key", virtual_key="synthetic-virtual-key",
        )
