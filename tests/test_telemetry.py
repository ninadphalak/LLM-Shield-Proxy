import pytest
from app.telemetry import TelemetryTracker
from app.config import settings


@pytest.mark.asyncio
async def test_telemetry_disabled_by_default(httpx_mock):
    # Verify default state: TELEMETRY_ENABLED = False -> zero egress
    settings.TELEMETRY_ENABLED = False
    settings.TELEMETRY_ENDPOINT_URL = None
    settings.TELEMETRY_API_KEY = None

    tracker = TelemetryTracker()
    assert tracker.is_enabled is False

    tracker.record_request(redactions_count=5)
    tracker.increment_active()

    # Attempting emission should return early with 0 HTTP requests
    await tracker.emit_telemetry()
    assert len(httpx_mock.get_requests()) == 0


@pytest.mark.asyncio
async def test_telemetry_opt_in_emission_success(httpx_mock):
    endpoint = "https://telemetry.example.com/rest/v1/telemetry_logs"
    api_key = "test_key_123"

    settings.TELEMETRY_ENABLED = True
    settings.TELEMETRY_ENDPOINT_URL = endpoint
    settings.TELEMETRY_API_KEY = api_key

    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        status_code=201
    )

    tracker = TelemetryTracker()
    assert tracker.is_enabled is True

    tracker.record_request(redactions_count=3)
    tracker.increment_active()

    await tracker.emit_telemetry()

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]

    assert req.headers["apikey"] == api_key
    assert req.headers["authorization"] == f"Bearer {api_key}"

    import json
    payload = json.loads(req.content.decode("utf-8"))
    assert "timestamp" in payload
    assert payload["active_proxy_connections"] == 1
    assert payload["total_requests_processed"] == 1
    assert payload["total_pii_redactions"] == 3

    # Reset settings back to default
    settings.TELEMETRY_ENABLED = False
    settings.TELEMETRY_ENDPOINT_URL = None
    settings.TELEMETRY_API_KEY = None


@pytest.mark.asyncio
async def test_telemetry_fails_silently_on_error(httpx_mock):
    endpoint = "https://telemetry.example.com/rest/v1/telemetry_logs"
    api_key = "test_key_123"

    settings.TELEMETRY_ENABLED = True
    settings.TELEMETRY_ENDPOINT_URL = endpoint
    settings.TELEMETRY_API_KEY = api_key

    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        status_code=500
    )

    tracker = TelemetryTracker()
    tracker.record_request(redactions_count=1)

    # Must fail silently without raising an exception
    await tracker.emit_telemetry()

    # Reset settings back to default
    settings.TELEMETRY_ENABLED = False
    settings.TELEMETRY_ENDPOINT_URL = None
    settings.TELEMETRY_API_KEY = None
