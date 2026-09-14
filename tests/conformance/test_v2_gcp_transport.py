"""The GCP transport: a phased deadline, a retry budget, and a token with a clock.

Three defects, all of the same kind and none of them a measurement error: each one ends a
BILLED multi-hour run partway through and writes nothing. The four `gcp-*` rows are the
most expensive artefacts in this repository to produce and the only ones that cannot be
re-run for free, so a transport that dies at case 24 of 32 costs the run twice.

  1. `urlopen(request, timeout=60)`. Every other socket this harness opens is bounded by
     `--connect-timeout` / `--read-timeout`; the two that cross the public internet to a
     commercial SaaS were bounded by a literal, so the ONE pair of calls the flags exist
     for was the one pair they could not reach. And `urllib` takes ONE number, which
     cannot say "connecting may take five seconds but a silent response may not stall for
     sixty" -- the limitation the client path already solves with `httpx.Timeout` and
     documents at length above `CLIENT_CONNECT_TIMEOUT`.

  2. Nothing retried. DLP and Model Armor are quota'd per project per minute and this
     profile calls them once per DELTA, which is the shape that meets a quota wall. An
     unretried 429 propagates out of `feed()`, through the gateway handler, and ends the
     run at whichever case was in flight.

  3. `if not _GCP_TOKEN_CACHE:` -- fetch once, hold forever, against a token Google issues
     for one hour. A GCP row takes longer than an hour and `--exhaustive-splits`
     guarantees it, so every case past the 60-minute mark got a 401 and 401 is not
     retryable.

The rule the retry rules encode, and the reason 400 must NOT be retried: a refusal from
the vendor's transport says nothing about the vendor's DETECTOR, so it must surface as a
transport error and never be scored as a redaction result. Retrying a 400 would convert a
malformed request into six malformed requests and then into the same failure.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark import v2_emitter  # noqa: E402
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    _gcp_context,
    _gcp_opener,
    _gcp_post,
    _gcp_retry_delay,
)

# --------------------------------------------------------------------------------------
# A scripted transport. No network, no credentials, no billing -- the retry rules are
# decisions about status codes and the status codes can be handed over directly.
# --------------------------------------------------------------------------------------


class _Headers(dict):
    def get(self, key, default=None):  # case-insensitive, like email.message.Message
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _Body:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ScriptedOpener:
    """Replays a script: an int is a status to raise, a dict is a body to return."""

    def __init__(self, script):
        self.script = script
        self.requests = []

    def open(self, request):
        self.requests.append(dict(request.headers))
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if isinstance(step, tuple):
            status, headers = step
            raise HTTPError(request.full_url, status, "scripted", _Headers(headers), None)
        if isinstance(step, int):
            raise HTTPError(request.full_url, step, "scripted", _Headers({}), None)
        return _Body(step)


@pytest.fixture
def transport(monkeypatch):
    """Drive `_gcp_post` against a scripted transport with sleeps recorded, not taken."""

    def drive(script, **globals_):
        opener = _ScriptedOpener(script)
        slept: list[float] = []
        for name, value in globals_.items():
            monkeypatch.setattr(v2_emitter, name, value)
        monkeypatch.setattr(v2_emitter, "_gcp_opener", lambda c, r: opener)
        monkeypatch.setattr(v2_emitter.time, "sleep", slept.append)
        monkeypatch.setattr(
            v2_emitter,
            "_GCP_TOKEN_CACHE",
            {"project": "proj", "token": "TOK", "issued_at": time.monotonic()},
        )
        return opener, slept

    return drive


def test_a_429_is_retried_rather_than_ending_the_run(transport):
    """THE DEFECT: this 429 used to propagate out of feed() and end a billed run."""
    opener, slept = transport([429, {"item": {"value": "ok"}}])
    assert _gcp_post("https://dlp.example/v2:deidentify", {"a": 1}) == {
        "item": {"value": "ok"}
    }
    assert len(opener.requests) == 2, "the 429 was not retried"
    assert len(slept) == 1 and slept[0] >= 0.0, "no backoff was taken between attempts"


def test_a_503_is_retried(transport):
    """Backend load shedding. Same category as 429 and it was equally fatal."""
    opener, _ = transport([503, 503, {"item": {"value": "ok"}}])
    assert _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})["item"]["value"] == "ok"
    assert len(opener.requests) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_a_non_load_status_is_raised_immediately(transport, status):
    """NOT retried, and this is the load-bearing half of the rule.

    A 400 is a statement about the REQUEST. Retrying it six times turns one failure into
    six identical failures and a minute of backoff, and -- worse -- a retry rule wide
    enough to swallow it is wide enough to make a permanently broken call look
    intermittent. 401 is here for the same reason from the other side: the answer to an
    expired token is the refresh in `_gcp_context`, not a retry that re-sends it.
    """
    opener, _ = transport([status, {"item": {"value": "ok"}}])
    with pytest.raises(HTTPError) as caught:
        _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})
    assert caught.value.code == status
    assert len(opener.requests) == 1, f"{status} must not be retried"


def test_the_retry_budget_is_bounded(transport):
    """A service that answers 429 forever must stop the run, not run forever."""
    opener, slept = transport([429], GCP_MAX_ATTEMPTS=4)
    with pytest.raises(HTTPError):
        _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})
    assert len(opener.requests) == 4, "attempts must equal GCP_MAX_ATTEMPTS exactly"
    assert len(slept) == 3, "no sleep after the final attempt"


def test_retry_after_is_honoured_over_the_backoff(transport):
    """The service said seven seconds. Backing off less is how a rate limit gets longer."""
    _opener, slept = transport(
        [(429, {"Retry-After": "7"}), {"item": {"value": "ok"}}],
        GCP_BACKOFF_BASE=1.0,
        GCP_BACKOFF_CAP=60.0,
    )
    _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})
    assert slept == [7.0], f"exponential backoff would be <= 1.0s here; slept {slept}"


def test_retry_after_is_clamped_by_the_cap(transport):
    """A service asking for an hour must not silently stall the run for an hour."""
    _opener, slept = transport(
        [(429, {"Retry-After": "3600"}), {"item": {"value": "ok"}}], GCP_BACKOFF_CAP=30.0
    )
    _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})
    assert slept == [30.0]


def test_retry_after_accepts_the_http_date_form():
    """RFC 9110 10.2.3 allows a date. Ignoring it falls back to a SHORTER wait."""
    import datetime
    from email.utils import format_datetime

    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=42)
    error = HTTPError(
        "https://x", 429, "slow down", _Headers({"Retry-After": format_datetime(when)}), None
    )
    assert 35.0 <= _gcp_retry_delay(error, attempt=1) <= 43.0


def test_the_backoff_grows_and_never_touches_the_global_random():
    """Jitter comes from a dedicated generator.

    The corpus is drawn from `random.Random(seed)` and a seeded run must reproduce. If the
    retry path consumed global `random` state, reproducibility would depend on how many
    times Google happened to rate-limit the run.
    """
    import random

    error = HTTPError("https://x", 429, "slow down", _Headers({}), None)
    random.seed(1234)
    before = random.random()
    for attempt in range(1, 7):
        assert 0.0 <= _gcp_retry_delay(error, attempt) <= v2_emitter.GCP_BACKOFF_BASE * (
            2 ** (attempt - 1)
        )
    random.seed(1234)
    assert random.random() == before, "the retry path consumed global random state"


def test_the_token_is_re_read_on_every_attempt(monkeypatch):
    """A retry that sleeps across the refresh boundary must send the NEW token.

    Capturing the token once before the loop -- the obvious shape -- would defeat the
    refresh the loop had just waited through, which is the failure this whole file exists
    to prevent, one level down.
    """
    opener = _ScriptedOpener([429, {"item": {"value": "ok"}}])
    cache = {"project": "proj", "token": "OLD", "issued_at": time.monotonic()}
    monkeypatch.setattr(v2_emitter, "_GCP_TOKEN_CACHE", cache)
    monkeypatch.setattr(v2_emitter, "_gcp_opener", lambda c, r: opener)
    monkeypatch.setattr(
        v2_emitter.time, "sleep", lambda _s: cache.__setitem__("token", "NEW")
    )
    _gcp_post("https://dlp.example/v2:deidentify", {"a": 1})
    assert [r.get("Authorization") for r in opener.requests] == [
        "Bearer OLD",
        "Bearer NEW",
    ]


# --------------------------------------------------------------------------------------
# The token clock.
# --------------------------------------------------------------------------------------


@pytest.fixture
def gcloud(monkeypatch):
    """Replace the `gcloud` subprocess and the clock; return the call log and the clock."""
    calls: list[str] = []
    clock = {"t": 1000.0}

    def fake(_argv, what):
        calls.append(what)
        return f"{what}-{len(calls)}"

    monkeypatch.setattr(v2_emitter, "_gcloud", fake)
    monkeypatch.setattr(v2_emitter.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(v2_emitter, "_GCP_TOKEN_CACHE", {})
    monkeypatch.setattr(v2_emitter, "_GCP_TOKEN_LOCK", threading.Lock())
    return calls, clock


def test_the_token_is_held_inside_the_ttl_and_refreshed_past_it(gcloud):
    """THE DEFECT: `if not _GCP_TOKEN_CACHE:` had no clock at all.

    Google's access tokens live one hour and the GCP rows run longer than that, so this
    was not a hypothetical: the run died on 401 with most of the corpus measured and
    nothing written.
    """
    calls, clock = gcloud
    first, _project = _gcp_context()

    clock["t"] += v2_emitter.GCP_TOKEN_TTL_SECONDS - 1
    inside, _ = _gcp_context()
    assert inside == first, "a token inside its TTL was refetched"

    clock["t"] += 2
    past, _ = _gcp_context()
    assert past != first, "a token past its TTL was not refreshed -- the original defect"
    assert calls.count("token") == 2


def test_the_project_id_is_fetched_once_and_never_expires(gcloud):
    """Only the token carries a clock. The project id is not a credential."""
    calls, clock = gcloud
    _t, first = _gcp_context()
    clock["t"] += v2_emitter.GCP_TOKEN_TTL_SECONDS * 5
    _t, later = _gcp_context()
    assert first == later
    assert calls.count("project") == 1, f"gcloud was called for the project {calls} times"


def test_the_ttl_leaves_real_headroom_inside_googles_hour():
    """A TTL at or above 3600s would refresh only after the token was already dead."""
    assert v2_emitter.GCP_TOKEN_TTL_SECONDS <= 3300, (
        "the refresh must land well inside Google's one-hour token lifetime; a TTL this "
        "close to 3600 refreshes after the 401s have already started"
    )


# --------------------------------------------------------------------------------------
# The phased deadline, against a real TLS server on loopback.
#
# Structural assertions would not have caught the original defect -- `timeout=60` is
# structurally fine -- so these measure the socket.
# --------------------------------------------------------------------------------------


def _self_signed(tmp_path):
    pytest.importorskip("cryptography")
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


class _StallingTLSServer(threading.Thread):
    """Completes the handshake, then holds the response back for `stall` seconds."""

    daemon = True

    def __init__(self, certfile, keyfile, stall):
        super().__init__()
        self.stall = stall
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(certfile, keyfile)
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.port = self.listener.getsockname()[1]

    def run(self):
        while True:
            try:
                raw, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            conn = self.ctx.wrap_socket(raw, server_side=True)
            conn.recv(65536)
            time.sleep(self.stall)
            body = json.dumps({"item": {"value": "ok"}}).encode()
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            # Let the client drain before teardown, or Windows answers the read with an
            # RST and the test measures the teardown instead of the stall.
            time.sleep(0.5)
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        except Exception:  # noqa: BLE001 -- a torn-down client is the point of the test
            pass


def _trust_self_signed(opener, cafile):
    """Point every HTTPS handler at a context that trusts the test CA. Returns them.

    SELECT BY TYPE, NOT BY WHETHER `_context` IS ALREADY SET. The predecessor guarded on
    `getattr(handler, "_context", None) is not None`, which is a statement about the
    CPython version rather than about the handler: 3.12 and later build a default SSL
    context in `HTTPSHandler.__init__`, while 3.11 stores the `context=None` it was given.
    So on 3.11 the guard was false, NOTHING was patched, the self-signed certificate went
    to a verifying context, and both tests failed with CERTIFICATE_VERIFY_FAILED. They
    passed everywhere else, which is why it survived until this branch first reached CI.

    The empty-result assertion is the other half of the repair. A patch loop that silently
    matches nothing is the defect itself, not a precaution against it.
    """
    patched = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    for handler in patched:
        handler._context = ssl.create_default_context(cafile=cafile)
    assert patched, "no HTTPSHandler on the opener; the CA patch would be a silent no-op"
    return patched


def _trusting_opener(connect_timeout, read_timeout, cafile):
    """The PRODUCTION opener, with only the self-signed CA additionally trusted."""
    v2_emitter._GCP_OPENERS.clear()
    opener = _gcp_opener(connect_timeout, read_timeout)
    _trust_self_signed(opener, cafile)
    return opener


@pytest.fixture
def stalling_server(tmp_path):
    certfile, keyfile = _self_signed(tmp_path)
    server = _StallingTLSServer(certfile, keyfile, stall=4.0)
    server.start()
    yield server, certfile
    try:
        server.listener.close()
    except OSError:
        pass


def test_the_socket_carries_the_read_deadline_after_the_handshake(stalling_server):
    """The connect deadline must not survive the handshake as the read deadline.

    This is the whole mechanism: `urlopen(timeout=T)` gives the socket ONE number for
    both phases, so the phases are separated where urllib actually applies them.
    """
    server, certfile = stalling_server
    seen = {}
    v2_emitter._GCP_OPENERS.clear()
    opener = _gcp_opener(30.0, 7.0)
    for handler in _trust_self_signed(opener, certfile):
        original = handler.do_open

        def spy(cls, req, _original=original, **kw):
            class _Spy(cls):
                def connect(inner):
                    super().connect()
                    seen["after_connect"] = inner.sock.gettimeout()

            return _original(_Spy, req, **kw)

        handler.do_open = spy
    try:
        opener.open(Request(f"https://localhost:{server.port}/v1/x", data=b"{}"))
    except Exception:  # noqa: BLE001 -- the stall is expected; only the timeout matters
        pass
    assert seen.get("after_connect") == 7.0, (
        f"after connect the socket carried {seen.get('after_connect')}, not the 7.0s read "
        "deadline; the connect deadline leaked into the read phase"
    )


def test_a_short_read_deadline_trips_on_a_stall_and_a_long_one_does_not(stalling_server):
    """The read deadline is what decides. Nothing else differs between the two calls."""
    server, certfile = stalling_server
    url = f"https://localhost:{server.port}/v1/x"

    def call(read_timeout):
        return _trusting_opener(30.0, read_timeout, certfile).open(
            Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        )

    with pytest.raises((TimeoutError, OSError)):
        call(1.0)

    with call(8.0) as response:
        assert json.loads(response.read().decode()) == {"item": {"value": "ok"}}


def test_a_short_connect_deadline_bounds_the_dial_while_the_read_deadline_is_long():
    """Two numbers, not one under two names.

    198.51.100.0/24 is TEST-NET-2 (RFC 5737): routed nowhere, so the SYN goes unanswered
    and only the CONNECT deadline can end the attempt. Under the old single `timeout=60`
    this waited up to a minute per call.
    """
    v2_emitter._GCP_OPENERS.clear()
    opener = _gcp_opener(1.0, 60.0)
    started = time.monotonic()
    with pytest.raises(OSError):
        opener.open(Request("https://198.51.100.7/v1/x", data=b"{}"))
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, (
        f"the dial took {elapsed:.1f}s against a 1s connect deadline and a 60s read "
        "deadline; the read deadline is bounding the connect phase"
    )


def test_the_cloud_deadline_is_not_the_client_deadline():
    """Aliasing them would have been a silent 60s -> 10s regression on the billed calls.

    `CLIENT_READ_TIMEOUT` is 10s because the client talks to loopback. DLP on a cold
    project exceeds that, so routing the flags must not mean sharing the defaults.
    """
    assert v2_emitter.GCP_READ_TIMEOUT >= v2_emitter.CLIENT_READ_TIMEOUT
    assert v2_emitter.GCP_READ_TIMEOUT == 60.0, (
        "the default must reproduce the deadline the published GCP rows were measured "
        "under; changing it silently rescopes what a timeout in those rows means"
    )


def test_the_ca_patch_applies_when_the_handler_holds_no_context(tmp_path):
    """The 3.11 shape, pinned. A guard that cannot fail is not a guard.

    CPython 3.12 and later build a default SSL context inside `HTTPSHandler.__init__`;
    3.11 stores the `context=None` it was handed. The previous patch loop tested
    `_context is not None` and so did nothing at all on 3.11, which sent a self-signed
    certificate to a verifying context and failed two tests with CERTIFICATE_VERIFY_FAILED
    on that interpreter alone. This reproduces the 3.11 shape on any interpreter.
    """
    certfile, _keyfile = _self_signed(tmp_path)
    v2_emitter._GCP_OPENERS.clear()
    opener = _gcp_opener(30.0, 7.0)

    for handler in opener.handlers:
        if isinstance(handler, urllib.request.HTTPSHandler):
            handler._context = None

    patched = _trust_self_signed(opener, certfile)

    assert patched, "nothing was patched"
    for handler in patched:
        assert handler._context is not None
        assert handler._context.verify_mode == ssl.CERT_REQUIRED
