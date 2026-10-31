"""Regression tests for the 2026-10 audit: config/policy hot reload and the identity enforcer.

Dummy keys and in-process ES256 keys only; JWKS lookup is stubbed.
"""

import logging
import os

import pytest

from llm_shield_proxy.core.config import Settings, settings

_REVOCABLE = ("VALID_VIRTUAL_KEYS", "OVERRIDE_CLIENT_AUTH", "ENABLE_CANARY_TRIPWIRE", "SHIELD_WATERMARK_SECRET", "CANARY_TOKEN")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in _REVOCABLE:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Settings.reload: overlay-on-live-state
# ---------------------------------------------------------------------------


def test_reload_reverts_settings_deleted_from_dotenv(clean_env):
    env = clean_env / ".env"
    env.write_text("VALID_VIRTUAL_KEYS=vk-dummy-a,vk-dummy-b\nOVERRIDE_CLIENT_AUTH=true\n", encoding="utf-8")
    configured = Settings()
    configured.reload()
    assert configured.valid_virtual_keys_set == {"vk-dummy-a", "vk-dummy-b"}
    assert configured.OVERRIDE_CLIENT_AUTH is True

    # Operator revokes by deleting the lines.
    env.write_text("# keys revoked\n", encoding="utf-8")
    configured.reload()

    fresh = Settings()
    assert configured.VALID_VIRTUAL_KEYS == fresh.VALID_VIRTUAL_KEYS == ""
    assert configured.valid_virtual_keys_set == frozenset()
    assert configured.OVERRIDE_CLIENT_AUTH is False


@pytest.mark.parametrize("revoking_yaml", ("valid_virtual_keys:\n", "", "- not\n- a mapping\n"))
def test_reload_yaml_null_empty_or_non_mapping_means_no_override(clean_env, revoking_yaml):
    cfg = clean_env / "config.yaml"
    cfg.write_text("valid_virtual_keys: vk-dummy-c\noverride_client_auth: true\n", encoding="utf-8")
    configured = Settings()
    configured.reload()
    assert configured.valid_virtual_keys_set == {"vk-dummy-c"}
    assert configured.OVERRIDE_CLIENT_AUTH is True

    cfg.write_text(revoking_yaml, encoding="utf-8")
    configured.reload()

    assert configured.valid_virtual_keys_set == frozenset()
    assert configured.OVERRIDE_CLIENT_AUTH is False


def test_rejected_reload_is_logged_at_warning_without_values(clean_env, caplog):
    configured = Settings(VALID_VIRTUAL_KEYS="vk-keep")
    configured.valid_virtual_keys_set = {"vk-keep"}
    (clean_env / "config.yaml").write_text("port: secret-looking-value\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="llm_shield_proxy.core.config"):
        configured.reload()

    assert configured.valid_virtual_keys_set == {"vk-keep"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Rejected configuration reload" in r.getMessage()]
    assert warnings
    assert all("secret-looking-value" not in r.getMessage() for r in caplog.records)


def test_reload_keeps_generated_canary_token_stable(clean_env):
    (clean_env / ".env").write_text(
        "ENABLE_CANARY_TRIPWIRE=true\nSHIELD_WATERMARK_SECRET=dummy-secret\n", encoding="utf-8"
    )
    configured = Settings()
    token = configured.CANARY_TOKEN
    assert token
    configured.reload()
    assert configured.CANARY_TOKEN == token


# ---------------------------------------------------------------------------
# Settings.reload_policies: mtime-older-skip
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_policies(tmp_path):
    saved = (
        settings.POLICIES_FILE_PATH,
        settings._flattened_policies,
        settings._policies_mtime,
        settings._policies_path,
        settings._policies_digest,
    )
    yield tmp_path / "policies.yaml"
    (
        settings.POLICIES_FILE_PATH,
        settings._flattened_policies,
        settings._policies_mtime,
        settings._policies_path,
        settings._policies_digest,
    ) = saved


def test_policy_revert_to_older_mtime_file_is_applied_by_poll(isolated_policies):
    pol = isolated_policies
    settings.POLICIES_FILE_PATH = str(pol)
    old = pol.with_name("policies.prev.yaml")
    old.write_text("roles:\n  r: {}\nvirtual_keys:\n  vk-id-tenant-1: r\n", encoding="utf-8")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    pol.write_text("roles:\n  r: {}\nvirtual_keys:\n  vk-id-tenant-1: r\n  vk-id-tenant-2: r\n", encoding="utf-8")
    settings.reload_policies()
    assert set(settings._flattened_policies) == {"vk-id-tenant-1", "vk-id-tenant-2"}

    os.replace(old, pol)  # restore a backup with its older mtime preserved
    settings.reload_policies()  # what the background poll calls

    assert set(settings._flattened_policies) == {"vk-id-tenant-1"}


def test_policy_poll_skips_unchanged_content_and_force_still_reloads(isolated_policies, caplog):
    pol = isolated_policies
    settings.POLICIES_FILE_PATH = str(pol)
    pol.write_text("roles:\n  r: {}\nvirtual_keys:\n  vk-id-tenant-1: r\n", encoding="utf-8")
    settings.reload_policies()

    with caplog.at_level(logging.INFO, logger="llm_shield_proxy.core.config"):
        settings.reload_policies()
        assert "Successfully hot-reloaded" not in caplog.text
        settings.reload_policies(force=True)
        assert "Successfully hot-reloaded" in caplog.text
