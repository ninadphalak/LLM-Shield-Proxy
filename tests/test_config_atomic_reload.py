import pytest

from llm_shield_proxy.core.config import Settings

_SECURITY_BOOLEANS = (
    "OVERRIDE_CLIENT_AUTH",
    "INSECURE_SKIP_VERIFY",
    "ALLOW_CLIENT_UPSTREAM_OVERRIDE",
    "TELEMETRY_ENABLED",
)


def test_dotenv_string_false_is_parsed_for_security_booleans(monkeypatch, tmp_path):
    configured = Settings(**{name: True for name in _SECURITY_BOOLEANS})
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{name}=false" for name in _SECURITY_BOOLEANS) + "\n",
        encoding="utf-8",
    )
    for name in _SECURITY_BOOLEANS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    configured.reload()

    for name in _SECURITY_BOOLEANS:
        value = getattr(configured, name)
        assert value is False
        assert isinstance(value, bool)


def test_yaml_string_false_is_parsed_for_security_booleans(monkeypatch, tmp_path):
    configured = Settings(**{name: True for name in _SECURITY_BOOLEANS})
    (tmp_path / "config.yaml").write_text(
        "\n".join(f'{name}: "false"' for name in _SECURITY_BOOLEANS) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    configured.reload()

    for name in _SECURITY_BOOLEANS:
        value = getattr(configured, name)
        assert value is False
        assert isinstance(value, bool)


@pytest.mark.parametrize(
    "yaml_text",
    (
        'AIR_GAPPED_MODE: "true"\nEGRESS_GATEWAY_URL: http://gateway.internal:8080\n',
        'EGRESS_GATEWAY_URL: http://gateway.internal:8080\nAIR_GAPPED_MODE: "true"\n',
    ),
)
def test_air_gapped_yaml_reload_is_independent_of_key_order(monkeypatch, tmp_path, yaml_text):
    configured = Settings(AIR_GAPPED_MODE=False, EGRESS_GATEWAY_URL=None)
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    configured.reload()

    assert configured.AIR_GAPPED_MODE is True
    assert configured.EGRESS_GATEWAY_URL == "http://gateway.internal:8080"


@pytest.mark.parametrize(
    "yaml_text",
    (
        "AIR_GAPPED_MODE: true\nTELEMETRY_ENABLED: true\n",
        "TELEMETRY_ENABLED: true\nPORT: not-an-integer\n",
        "TELEMETRY_ENABLED: true\n[unterminated",
    ),
)
def test_invalid_yaml_reload_preserves_all_existing_state(monkeypatch, tmp_path, yaml_text):
    configured = Settings(
        AIR_GAPPED_MODE=False,
        EGRESS_GATEWAY_URL=None,
        TELEMETRY_ENABLED=False,
        PORT=8123,
        VALID_VIRTUAL_KEYS="key-one,key-two",
    )
    configured.valid_virtual_keys_set = {"key-one", "key-two"}
    before_fields = configured.model_dump(mode="python")
    before_private = dict(configured.__pydantic_private__)
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    configured.reload()

    assert configured.model_dump(mode="python") == before_fields
    assert configured.__pydantic_private__ == before_private


def test_invalid_dotenv_reload_preserves_all_existing_state(monkeypatch, tmp_path):
    configured = Settings(TELEMETRY_ENABLED=False, PORT=8123, VALID_VIRTUAL_KEYS="key-one")
    configured.valid_virtual_keys_set = {"key-one"}
    before_fields = configured.model_dump(mode="python")
    before_private = dict(configured.__pydantic_private__)
    (tmp_path / ".env").write_text(
        "TELEMETRY_ENABLED=true\nPORT=not-an-integer\nVALID_VIRTUAL_KEYS=changed-key\n",
        encoding="utf-8",
    )
    for name in ("TELEMETRY_ENABLED", "PORT", "VALID_VIRTUAL_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    configured.reload()

    assert configured.model_dump(mode="python") == before_fields
    assert configured.__pydantic_private__ == before_private


def test_direct_multi_field_mutation_can_be_restored_without_partial_validation():
    configured = Settings(AIR_GAPPED_MODE=False, EGRESS_GATEWAY_URL=None)

    # Test fixtures update related fields one at a time and often restore them in
    # reverse order. These internal assignments must not fail midway and leave the
    # shared settings object poisoned for later tests.
    configured.AIR_GAPPED_MODE = True
    configured.EGRESS_GATEWAY_URL = "http://gateway.internal:8080"
    configured.EGRESS_GATEWAY_URL = None
    configured.AIR_GAPPED_MODE = False

    assert configured.AIR_GAPPED_MODE is False
    assert configured.EGRESS_GATEWAY_URL is None
