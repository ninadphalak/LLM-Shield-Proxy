from llm_shield_proxy.core.config import Settings


def test_reload_does_not_override_process_environment_with_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEMETRY_ENABLED=true\nTELEMETRY_ENDPOINT_URL=https://example.invalid\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("TELEMETRY_ENDPOINT_URL", "")

    configured = Settings()
    configured.reload()

    assert configured.TELEMETRY_ENABLED is False
    assert configured.TELEMETRY_ENDPOINT_URL == ""
