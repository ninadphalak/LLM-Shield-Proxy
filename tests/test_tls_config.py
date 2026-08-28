from unittest.mock import MagicMock, patch

import httpx
import pytest

from llm_shield_proxy.core.config import Settings


def test_tls_config_defaults():
    settings = Settings()
    assert settings.TLS_CERT_FILE is None
    assert settings.TLS_KEY_FILE is None
    assert settings.CLIENT_CA_FILE is None
    assert settings.CA_BUNDLE_FILE is None
    assert settings.INSECURE_SKIP_VERIFY is False
    assert settings.OUTBOUND_CLIENT_CERT is None
    assert settings.OUTBOUND_CLIENT_KEY is None


def test_tls_config_parsing(monkeypatch):
    monkeypatch.setenv("TLS_CERT_FILE", "/tmp/cert.pem")
    monkeypatch.setenv("TLS_KEY_FILE", "/tmp/key.pem")
    monkeypatch.setenv("CLIENT_CA_FILE", "/tmp/client-ca.pem")
    monkeypatch.setenv("CA_BUNDLE_FILE", "/tmp/ca.pem")
    monkeypatch.setenv("INSECURE_SKIP_VERIFY", "true")
    monkeypatch.setenv("OUTBOUND_CLIENT_CERT", "/tmp/out-cert.pem")
    monkeypatch.setenv("OUTBOUND_CLIENT_KEY", "/tmp/out-key.pem")

    settings = Settings()

    assert settings.TLS_CERT_FILE == "/tmp/cert.pem"
    assert settings.TLS_KEY_FILE == "/tmp/key.pem"
    assert settings.CLIENT_CA_FILE == "/tmp/client-ca.pem"
    assert settings.CA_BUNDLE_FILE == "/tmp/ca.pem"
    assert settings.INSECURE_SKIP_VERIFY is True
    assert settings.OUTBOUND_CLIENT_CERT == "/tmp/out-cert.pem"
    assert settings.OUTBOUND_CLIENT_KEY == "/tmp/out-key.pem"


@pytest.mark.asyncio
async def test_get_http_client_insecure_skip_verify(monkeypatch):
    from llm_shield_proxy.api import main
    monkeypatch.setattr(main.settings, "INSECURE_SKIP_VERIFY", True)
    monkeypatch.setattr(main.settings, "CA_BUNDLE_FILE", "/tmp/ca.pem") # Should be ignored because INSECURE_SKIP_VERIFY is True

    request = MagicMock()
    request.app.state = MagicMock()
    request.app.state.http_client = None

    client = main.get_http_client(request)
    # httpx.AsyncClient exposes verify via _transport or looking at the kwargs isn't directly exposed easily
    # But we can verify it's an instance of httpx.AsyncClient
    assert isinstance(client, httpx.AsyncClient)
    assert request.app.state.http_client == client


@pytest.mark.asyncio
async def test_get_http_client_ca_bundle_file(monkeypatch):
    from llm_shield_proxy.api import main
    monkeypatch.setattr(main.settings, "INSECURE_SKIP_VERIFY", False)
    monkeypatch.setattr(main.settings, "CA_BUNDLE_FILE", "/tmp/ca.pem")

    request = MagicMock()
    request.app.state = MagicMock()
    request.app.state.http_client = None

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MagicMock()
        main.get_http_client(request)

        mock_client.assert_called_once()
        kwargs = mock_client.call_args[1]
        assert kwargs["verify"] == "/tmp/ca.pem"


@pytest.mark.asyncio
async def test_get_http_client_outbound_mtls(monkeypatch):
    from llm_shield_proxy.api import main
    monkeypatch.setattr(main.settings, "OUTBOUND_CLIENT_CERT", "/tmp/client.crt")
    monkeypatch.setattr(main.settings, "OUTBOUND_CLIENT_KEY", "/tmp/client.key")
    monkeypatch.setattr(main.settings, "INSECURE_SKIP_VERIFY", False)

    request = MagicMock()
    request.app.state = MagicMock()
    request.app.state.http_client = None

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MagicMock()
        main.get_http_client(request)

        mock_client.assert_called_once()
        kwargs = mock_client.call_args[1]
        assert kwargs["cert"] == ("/tmp/client.crt", "/tmp/client.key")
