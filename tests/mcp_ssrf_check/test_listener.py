"""The callback listener records only tokens the checker minted and echoes nothing else."""

import urllib.error
import urllib.request

from mcp_ssrf_check.listener import CallbackListener


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - loopback listener under test
            return response.status, response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers


def test_minted_tokens_are_recorded_and_redirected():
    with CallbackListener() as listener:
        listener.expect("abc123-direct")
        listener.expect("abc123-redirect")
        status, _ = _get(f"http://127.0.0.1:{listener.port}/hit/abc123-direct")
        assert status == 200
        assert listener.seen("abc123-direct")

        opener = urllib.request.build_opener(_NoRedirect)
        response = opener.open(f"http://127.0.0.1:{listener.port}/redirect/abc123-redirect", timeout=2)
        assert response.status == 302
        assert response.headers["Location"] == f"http://127.0.0.1:{listener.port}/hit/abc123-redirect-redirected"
        assert listener.seen("abc123-redirect")


def test_unregistered_tokens_are_refused_and_never_recorded():
    with CallbackListener() as listener:
        listener.expect("registered")
        for path in ("/hit/%0d%0aX-Injected:%201", "/redirect/..%2Fetc", "/hit/", "/hit/not-registered", "/redirect/not-registered", "/anything"):
            status, headers = _get(f"http://127.0.0.1:{listener.port}{path}")
            assert status in (200, 404), path
            assert "X-Injected" not in headers
            assert "Location" not in headers
        assert listener.recorder.hits == {}


def test_redirect_points_at_the_configured_target():
    with CallbackListener(redirect_host="localhost") as listener:
        listener.expect("tok")
        opener = urllib.request.build_opener(_NoRedirect)
        response = opener.open(f"http://127.0.0.1:{listener.port}/redirect/tok", timeout=2)
        assert response.headers["Location"] == f"http://localhost:{listener.port}/hit/tok-redirected"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        fp.status = code
        return fp
