import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "integrations"


def _yaml(relative_path: str):
    with (EXAMPLES / relative_path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_integration_python_examples_parse_without_importing_optional_sdks():
    for name in ("langchain_chat.py", "llamaindex_chat.py", "mcp_jsonrpc.py"):
        ast.parse((EXAMPLES / name).read_text(encoding="utf-8"), filename=name)


def test_litellm_compose_routes_shield_to_litellm():
    compose = _yaml("litellm/docker-compose.yml")
    shield_env = compose["services"]["llm-shield"]["environment"]
    assert shield_env["UPSTREAM_BASE_URL"] == "http://litellm:4000"
    assert shield_env["TELEMETRY_ENABLED"] == "false"

    config = _yaml("litellm/config.yaml")
    assert config["model_list"][0]["model_name"] == "shield-demo"


def test_openwebui_compose_uses_openai_compatible_v1_boundary():
    compose = _yaml("openwebui/docker-compose.yml")
    webui_env = compose["services"]["open-webui"]["environment"]
    assert webui_env["OPENAI_API_BASE_URL"] == "http://llm-shield:8000/v1"
    assert webui_env["OPENAI_API_KEY"].startswith("sk-shield-")


def test_envoy_example_is_fail_closed_and_uses_documented_body_modes():
    envoy = _yaml("envoy/envoy.yaml")
    filters = envoy["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"][
        "http_filters"
    ]
    ext_proc = filters[0]["typed_config"]
    assert ext_proc["failure_mode_allow"] is False
    assert ext_proc["processing_mode"]["request_body_mode"] == "BUFFERED"
    assert ext_proc["processing_mode"]["response_body_mode"] == "STREAMED"
    assert ext_proc["grpc_service"]["envoy_grpc"]["cluster_name"] == "llm_shield_ext_proc"
