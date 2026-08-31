import asyncio
import logging
from unittest.mock import MagicMock, patch

import httpx
import orjson
import pytest

from llm_shield_proxy.compliance.trace_exporter import DecisionTraceExporter
from llm_shield_proxy.compliance.transport import AsyncWebhookTransport, SidecarFileTransport


@pytest.fixture
def oscal_payload():
    return {"test": "payload"}


@pytest.mark.asyncio
async def test_webhook_transport_fire_and_forget_failure(caplog, oscal_payload):
    """
    Test that the webhook transport fails safely without crashing the system when it hits a timeout or connection error.
    """
    # Use an unroutable IP to simulate a timeout/failure.
    # But since we want the test to run fast, we will mock httpx.AsyncClient.post instead.
    transport = AsyncWebhookTransport(webhook_url="http://192.0.2.1/webhook", timeout=0.1)

    # 1. Test TimeoutException
    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Mocked timeout")):
        with caplog.at_level(logging.WARNING):
            await transport.dispatch(oscal_payload)
        assert "GRC webhook transport timed out" in caplog.text

    caplog.clear()

    # 2. Test RequestError
    with patch("httpx.AsyncClient.post", side_effect=httpx.RequestError("Mocked request error")):
        with caplog.at_level(logging.WARNING):
            await transport.dispatch(oscal_payload)
        assert "GRC webhook transport request failed" in caplog.text

    caplog.clear()

    # 3. Test generic Exception
    with patch("httpx.AsyncClient.post", side_effect=Exception("Unexpected boom")):
        with caplog.at_level(logging.ERROR):
            await transport.dispatch(oscal_payload)
        assert "Unexpected GRC webhook transport error" in caplog.text

    caplog.clear()

    # 4. Test an HTTP error response. A completed POST is not successful delivery.
    error_response = httpx.Response(
        500,
        request=httpx.Request("POST", "https://example.invalid/grc"),
    )
    with patch("httpx.AsyncClient.post", return_value=error_response):
        with caplog.at_level(logging.WARNING):
            await transport.dispatch(oscal_payload)
        assert "received HTTP 500" in caplog.text


@pytest.mark.asyncio
async def test_sidecar_file_transport(tmp_path, oscal_payload):
    """
    Test that the SidecarFileTransport writes JSONL format correctly.
    """
    log_file = tmp_path / "oscal.jsonl"
    transport = SidecarFileTransport(file_path=str(log_file))

    await transport.dispatch(oscal_payload)
    await transport.dispatch(oscal_payload)

    # Read back and verify
    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2

    parsed1 = orjson.loads(lines[0])
    parsed2 = orjson.loads(lines[1])
    assert parsed1 == oscal_payload
    assert parsed2 == oscal_payload


@pytest.mark.asyncio
async def test_trace_exporter_async_dispatch():
    """
    Test that DecisionTraceExporter dispatches to multiple transports asynchronously.
    """
    mock_transport1 = MagicMock()
    mock_transport2 = MagicMock()

    # Needs an async mock for the dispatch method
    async def mock_dispatch1(payload):
        mock_transport1.payload = payload

    async def mock_dispatch2(payload):
        mock_transport2.payload = payload

    mock_transport1.dispatch = mock_dispatch1
    mock_transport2.dispatch = mock_dispatch2

    exporter = DecisionTraceExporter(transports=[mock_transport1, mock_transport2])

    # Must run inside an async function so get_running_loop() finds a loop
    exporter.record_decision(
        tenant_id="tenant-123",
        virtual_key_hash="vk_hash",
        redacted_prompt_hash="prompt_hash",
        tool_name="get_weather",
        rbac_decision="ALLOW",
        payload_entropy=3.14,
    )

    # Let the event loop process the background tasks
    await asyncio.sleep(0.01)

    assert hasattr(mock_transport1, "payload")
    assert hasattr(mock_transport2, "payload")

    # Verify payload format
    payload = mock_transport1.payload
    assert "assessment-results" in payload
    assert payload["assessment-results"]["results"][0]["observations"][0]["title"] == "Decision for get_weather"
