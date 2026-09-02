import json
import logging
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import yaml

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings
from llm_shield_proxy.observability.audit import AuditLogger


@pytest.fixture(autouse=True)
def reset_policy_settings():
    old_file_path = settings.POLICIES_FILE_PATH
    old_flattened = settings._flattened_policies
    old_mtime = settings._policies_mtime
    old_failure_mode = settings.SHIELD_FAILURE_MODE
    old_override_auth = settings.OVERRIDE_CLIENT_AUTH
    old_upstream_key = settings.UPSTREAM_API_KEY
    old_finops = settings.ENABLE_FINOPS_METERING
    old_canary_token = settings.CANARY_TOKEN
    yield
    settings.POLICIES_FILE_PATH = old_file_path
    settings._flattened_policies = old_flattened
    settings._policies_mtime = old_mtime
    settings.SHIELD_FAILURE_MODE = old_failure_mode
    settings.OVERRIDE_CLIENT_AUTH = old_override_auth
    settings.UPSTREAM_API_KEY = old_upstream_key
    settings.ENABLE_FINOPS_METERING = old_finops
    settings.CANARY_TOKEN = old_canary_token
    from llm_shield_proxy.core.config import request_policy_ctx
    request_policy_ctx.set({})


@pytest.fixture
def temp_policies_file():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
        policies = {
            "roles": {
                "strict_role": {
                    "ENABLE_CANARY_TRIPWIRE": True,
                    "ENABLE_BLAST_RADIUS_LIMITS": True,
                    "ENABLE_FINOPS_METERING": True,
                    "ENABLE_TIER3_ONNX_NER": True,
                },
                "lax_role": {
                    "ENABLE_CANARY_TRIPWIRE": False,
                    "ENABLE_BLAST_RADIUS_LIMITS": False,
                    "ENABLE_FINOPS_METERING": False,
                    "ENABLE_TIER3_ONNX_NER": False,
                }
            },
            "virtual_keys": {
                "strict-tenant-id": "strict_role",
                "lax-tenant-id": "lax_role"
            },
            "default_role": "strict_role"
        }
        yaml.dump(policies, f)
        temp_path = f.name

    yield temp_path

    if os.path.exists(temp_path):
        os.remove(temp_path)


@pytest.mark.asyncio
async def test_policy_zero_trust_block(temp_policies_file):
    settings.POLICIES_FILE_PATH = temp_policies_file
    settings.SHIELD_FAILURE_MODE = "FAIL_CLOSED"
    settings.OVERRIDE_CLIENT_AUTH = True
    settings.UPSTREAM_API_KEY = "test"
    settings.reload_policies(force=True)

    # Remove default_role to enforce zero trust
    with open(temp_policies_file, "r") as f:
        policies = yaml.safe_load(f)
    del policies["default_role"]
    with open(temp_policies_file, "w") as f:
        yaml.dump(policies, f)
    settings.reload_policies(force=True)

    with patch("llm_shield_proxy.api.main.get_virtual_key_id", return_value="unknown-tenant"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            # Using a tenant ID that is not in the virtual_keys map
            response = await client.post(
                "/v1/chat/completions",
                headers={"authorization": "Bearer unknown-tenant"},
                json={"messages": [{"role": "user", "content": "Hello"}]}
            )
            assert response.status_code == 403
            assert response.json()["error"]["message"] == "Unauthorized virtual key"

@pytest.mark.asyncio
async def test_policy_role_overrides(temp_policies_file):
    settings.POLICIES_FILE_PATH = temp_policies_file
    settings.SHIELD_FAILURE_MODE = "FAIL_CLOSED"
    settings.OVERRIDE_CLIENT_AUTH = True
    settings.UPSTREAM_API_KEY = "test"
    settings.ENABLE_FINOPS_METERING = False
    settings.CANARY_TOKEN = "TEST_CANARY"
    # The strict role enables the canary tripwire, and the watermark is derived from
    # SHIELD_WATERMARK_SECRET. Without one the request path fails CLOSED with a 503 --
    # correct behaviour, but it meant this test only passed on a machine with an
    # untracked `.env`, and it has been red in CI since 2026-08-30. Startup validation
    # covers ENABLE_CANARY_TRIPWIRE; a role that turns the canary on per-request is not
    # covered by it.
    settings.SHIELD_WATERMARK_SECRET = "test-watermark-secret-not-a-real-secret"
    settings.reload_policies(force=True)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(200, request=httpx.Request("POST", "http://test"), json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 10}})
    mock_client.send.return_value = mock_response
    mock_client.request.return_value = mock_response
    mock_client.build_request.return_value = httpx.Request("POST", "http://upstream")

    print(f"Mock client request type: {type(mock_client.request)}")
    print(f"Mock client request return type: {type(mock_client.request.return_value)}")

    with patch("llm_shield_proxy.api.main.get_http_client", return_value=mock_client):
        with patch("llm_shield_proxy.api.main.get_virtual_key_id", side_effect=lambda k: "strict-tenant-id" if "strict" in k else "lax-tenant-id"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                # 1. Strict Role (enables Canary)
                await client.post(
                    "/v1/chat/completions",
                    headers={"authorization": "Bearer strict-tenant-id"},
                    json={"messages": [{"role": "user", "content": "Hello"}]}
                )

                call_args = mock_client.request.call_args[1]
                content = json.loads(call_args["content"])
                print("ACTUAL CONTENT:", content)
                # Canary should be injected
                assert "\u200d" in content["messages"][0]["content"]

                # 2. Lax Role (disables Canary)
                await client.post(
                    "/v1/chat/completions",
                    headers={"authorization": "Bearer lax-tenant-id"},
                    json={"messages": [{"role": "user", "content": "Hello"}]}
                )
                call_args_lax = mock_client.request.call_args[1]
                content_lax = json.loads(call_args_lax["content"])
                assert "\u200d" not in content_lax["messages"][0]["content"]


@pytest.mark.asyncio
async def test_policy_hot_reload(temp_policies_file):
    settings.POLICIES_FILE_PATH = temp_policies_file
    settings.reload_policies(force=True)

    assert settings._flattened_policies["lax-tenant-id"]["ENABLE_CANARY_TRIPWIRE"] is False

    # Modify file
    with open(temp_policies_file, "r") as f:
        policies = yaml.safe_load(f)
    policies["roles"]["lax_role"]["ENABLE_CANARY_TRIPWIRE"] = True
    with open(temp_policies_file, "w") as f:
        yaml.dump(policies, f)

    settings.reload_policies(force=True)
    assert settings._flattened_policies["lax-tenant-id"]["ENABLE_CANARY_TRIPWIRE"] is True


@pytest.mark.asyncio
async def test_audit_log_applied_role_name(temp_policies_file):
    settings.POLICIES_FILE_PATH = temp_policies_file
    settings.OVERRIDE_CLIENT_AUTH = True
    settings.UPSTREAM_API_KEY = "test"
    settings.reload_policies(force=True)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(200, request=httpx.Request("POST", "http://test"), json={"choices": [{"message": {"content": "ok"}}]})
    mock_client.send.return_value = mock_response
    mock_client.request.return_value = mock_response
    mock_client.build_request.return_value = httpx.Request("POST", "http://upstream")

    with patch("llm_shield_proxy.api.main.get_http_client", return_value=mock_client):
        with patch("llm_shield_proxy.api.main.get_virtual_key_id", return_value="strict-tenant-id"):
            with patch.object(AuditLogger, 'log_redaction_event') as mock_log:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    await client.post(
                        "/v1/chat/completions",
                        headers={"authorization": "Bearer strict-tenant-id"},
                        json={"messages": [{"role": "user", "content": "Hello"}]}
                    )

                # Check that applied_role_name was passed
                found = False
                for call in mock_log.mock_calls:
                    if call.kwargs.get("applied_role_name") == "strict-tenant-id":
                        found = True
                        break
                assert found, "AuditLogger should be called with applied_role_name='strict-tenant-id'"


def test_forced_reload_records_the_file_mtime(temp_policies_file, caplog):
    """A forced reload must not raise inside its own try block.

    `current_mtime` was read only on the non-forced path while the success branch
    assigned it unconditionally, so EVERY `reload_policies(force=True)` raised
    UnboundLocalError. The broad handler swallowed it and logged "Failed loading
    policies YAML configuration", which said the opposite of what had happened: the
    policies were applied, but the mtime bookkeeping and the success log were lost.
    Nothing caught it because the error was only ever a log line.
    """
    settings.POLICIES_FILE_PATH = temp_policies_file
    settings._policies_mtime = 0.0

    with caplog.at_level(logging.ERROR):
        settings.reload_policies(force=True)

    assert "Failed loading policies YAML configuration" not in caplog.text
    assert settings._policies_mtime == os.path.getmtime(temp_policies_file)
    assert settings._flattened_policies["strict-tenant-id"]["ENABLE_CANARY_TRIPWIRE"] is True
