"""Regression tests for the 2026-10 audit: config/policy hot reload and the identity enforcer.

Dummy keys and in-process ES256 keys only; JWKS lookup is stubbed.
"""

import base64
import logging
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

import llm_shield_proxy.security.identity as ident
from llm_shield_proxy.core.config import Settings, request_policy_ctx, settings

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


# ---------------------------------------------------------------------------
# verify_agent_identity: real ES256 tokens, JWKS stubbed
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.dummy"
PROXY_AUD = "https://shield.dummy"
URL = "http://proxy.dummy/v1/chat/completions"


def _b64(i: int) -> str:
    return base64.urlsafe_b64encode(i.to_bytes(32, "big")).rstrip(b"=").decode()


class _URL(str):
    def __new__(cls, value, path):
        obj = str.__new__(cls, value)
        obj.path = path
        return obj

    def replace(self, **kw):
        return self


class _Req:
    def __init__(self, access, proof, url=URL, path="/v1/chat/completions"):
        self.headers = {"Authorization": f"Bearer {access}", "DPoP": proof}
        self.method = "POST"
        self.url = _URL(url, path)

        class S:
            pass

        self.state = S()


@pytest.fixture
def idp(monkeypatch):
    iss_key = ec.generate_private_key(ec.SECP256R1())
    cli_key = ec.generate_private_key(ec.SECP256R1())
    pn = cli_key.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "x": _b64(pn.x), "y": _b64(pn.y)}
    jkt = ident._get_jwk_thumbprint(jwk)

    class _SK:
        key = iss_key.public_key()

    async def _fake_signing_key(issuer, token):
        return _SK()

    monkeypatch.setattr(ident, "_get_signing_key", _fake_signing_key)
    monkeypatch.setattr(settings, "AGENT_IDENTITY_ENFORCER", "strict")
    monkeypatch.setattr(settings, "ALLOWED_ISSUERS", [ISSUER])
    monkeypatch.setattr(settings, "ALLOWED_AUDIENCES", [PROXY_AUD])
    request_policy_ctx.set({})
    ident._dpop_replay_cache.clear()

    counter = {"n": 0}

    def access(**overrides):
        claims = {"iss": ISSUER, "aud": PROXY_AUD, "sub": "agent-dummy", "exp": int(time.time()) + 3600, "cnf": {"jkt": jkt}}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, iss_key, algorithm="ES256")

    def proof(htu=URL, iat=None, jti=None):
        counter["n"] += 1
        claims = {"htm": "POST", "htu": htu, "iat": int(iat if iat is not None else time.time()), "jti": jti or f"jti-{counter['n']}"}
        return jwt.encode(claims, cli_key, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": jwk})

    return access, proof


async def _verify(req, policy=None):
    await ident.verify_agent_identity(req, policy)
    return req.state.agent_identity_claim


@pytest.mark.asyncio
async def test_role_without_audiences_inherits_global_and_rejects_foreign_audience(idp):
    access, proof = idp
    role = {"allowed_issuers": [ISSUER]}  # no allowed_audiences
    with pytest.raises(HTTPException) as exc:
        await _verify(_Req(access(aud="https://other-service.dummy"), proof()), role)
    assert exc.value.status_code == 401
    assert await _verify(_Req(access(), proof()), role) == "agent-dummy"


@pytest.mark.asyncio
async def test_empty_effective_audience_list_rejects(idp, monkeypatch):
    access, proof = idp
    monkeypatch.setattr(settings, "ALLOWED_AUDIENCES", [])
    with pytest.raises(HTTPException):
        await _verify(_Req(access(), proof()))
    with pytest.raises(HTTPException):
        await _verify(_Req(access(), proof()), {"allowed_issuers": [ISSUER], "allowed_audiences": []})


@pytest.mark.asyncio
async def test_role_audience_list_takes_precedence(idp):
    access, proof = idp
    role = {"allowed_issuers": [ISSUER], "allowed_audiences": ["https://tenant.dummy"]}
    with pytest.raises(HTTPException):
        await _verify(_Req(access(), proof()), role)
    assert await _verify(_Req(access(aud="https://tenant.dummy"), proof()), role) == "agent-dummy"
    # A list-valued aud claim is accepted when it contains an allowed audience.
    assert await _verify(_Req(access(aud=["x", "https://tenant.dummy"]), proof()), role) == "agent-dummy"


@pytest.mark.asyncio
async def test_access_token_without_exp_rejected(idp):
    access, proof = idp
    with pytest.raises(HTTPException):
        await _verify(_Req(access(exp=None), proof()))
