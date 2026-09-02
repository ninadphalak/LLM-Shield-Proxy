"""A measurement is not a verdict.

Cloudflare AI Gateway is caching, rate limiting, observability and routing. It does not
claim to redact PII. Scoring it "Fail" on a privacy benchmark measures it against
something it never offered -- a smear, not retractable, and the end of any claim to be a
neutral referee. The same trap was already recorded for Presidio's replace/hash/mask
operators, which leak nothing and cannot rehydrate.

So the publishable `outcome` is derived from what the product CLAIMS plus what was
CONFIGURED, and only then from the measurement. `passed` stays the raw measurement and
is never overwritten, so negative findings survive.

The load-bearing property is that `outcome` is DERIVED, never typed. If a submitter
could write it, a vendor facing a real failure would write "not-applicable". The schema
re-derives the same rule in BOTH directions so a hand-edited report is invalid.
"""

import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pii_leak_benchmark.http_profile import (
    extract_fixture,
    run_http_conformance,
)
from pii_leak_benchmark.redaction_claim import (
    derive_outcome,
    normalize_claim,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (REPO_ROOT / "spec" / "v1.0.0" / "http-profile.schema.json").read_text(encoding="utf-8")
)


def _errors(document):
    return list(Draft202012Validator(SCHEMA).iter_errors(document))


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


# Per-request now; see extract_fixture.


def _gateway(port, redact=True):
    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            import urllib.request

            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
            prompt = payload["messages"][-1]["content"]
            # Values vary per run: recover them from the prompt by format.
            RAW = list(extract_fixture(prompt).values())
            MASK = {value: f"[TOK_{index}]" for index, value in enumerate(RAW)}
            forwarded = prompt
            if redact:
                for raw, token in MASK.items():
                    forwarded = forwarded.replace(raw, token)
            payload["messages"][-1]["content"] = forwarded
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=json.dumps(payload).encode(),
                        headers={"content-type": "application/json"},
                    ),
                    timeout=30,
                ).read()
            except Exception:  # noqa: BLE001
                pass
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Gateway


def _run(port, redact=True, **kwargs):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _gateway(port, redact))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=port,
            **kwargs,
        )
    finally:
        server.shutdown()
        server.server_close()


CLAIMED_AND_ON = {
    "vendor_claims_pii_redaction": "claimed",
    "claim_citation": "https://example.invalid/docs/pii-guardrails",
    "configured_for_this_run": True,
    "configuration_reference": "guardrail=pii-redact; all entity types",
}
CLAIMED_AND_OFF = {
    "vendor_claims_pii_redaction": "claimed",
    "claim_citation": "https://example.invalid/docs/pii-guardrails",
    "configured_for_this_run": False,
}
NOT_OFFERED = {
    "vendor_claims_pii_redaction": "not-offered",
    "claim_citation": "https://example.invalid/docs/ (caching, routing, observability)",
}


# ------------------------------------------------------------------ the four cases


def test_no_claim_recorded_is_not_publishable(capture_port):
    """Fail-closed default. You may not say anything until you state the claim."""
    report = _run(capture_port)
    assert report["passed"] is True
    assert report["outcome"] == "claim-unstated"
    assert "not publishable" in report["outcome_rationale"]
    assert _errors(report) == []


def test_product_with_no_redaction_feature_is_not_a_failure(capture_port):
    """The Cloudflare case: a leaking measurement is NOT a Fail for this product.

    This is the whole point. The gateway forwards the fixture verbatim -- every leak
    check fires -- and the outcome must still not be a verdict against a product that
    never offered redaction.
    """
    report = _run(capture_port, redact=False, redaction_claim=NOT_OFFERED)
    boundary = report["checks"]["configured_upstream_boundary"]

    # The measurement is preserved in full. Negative findings are never suppressed.
    assert report["passed"] is False
    assert sorted(boundary["leaked_entity_types"]) == ["CREDIT_CARD", "EMAIL", "SSN"]

    # But the publishable outcome is not a failure.
    assert report["outcome"] == "not-applicable"
    assert report["outcome"] != "fail"
    assert "never offered" in report["outcome_rationale"]
    assert _errors(report) == []


def test_redaction_available_but_not_enabled_is_a_configuration_statement(capture_port):
    report = _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_OFF)
    assert report["passed"] is False
    assert report["outcome"] == "redaction-not-enabled"
    assert "not a verdict" in report["outcome_rationale"]
    assert _errors(report) == []


def test_redaction_claimed_and_enabled_and_leaking_is_a_real_fail(capture_port):
    """The one case that IS an accusation, and it requires all three conditions."""
    report = _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_ON)
    assert report["passed"] is False
    assert report["outcome"] == "fail"
    assert report["checks"]["configured_upstream_boundary"]["leaked_entity_types"]
    assert _errors(report) == []


def test_redaction_claimed_and_enabled_and_clean_is_a_real_pass(capture_port):
    report = _run(capture_port, redaction_claim=CLAIMED_AND_ON)
    assert report["outcome"] == "pass"
    assert report["passed"] is True
    assert _errors(report) == []


def test_the_three_cases_are_distinguishable_from_the_json_alone(capture_port):
    """A reader with only the JSON must be able to tell them apart."""
    outcomes = {
        _run(capture_port, redact=False, redaction_claim=NOT_OFFERED)["outcome"],
        _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_OFF)["outcome"],
        _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_ON)["outcome"],
    }
    assert outcomes == {"not-applicable", "redaction-not-enabled", "fail"}


# ------------------------------------------------------- the claim must be sourced


def test_a_claim_without_a_citation_is_refused():
    """An unsourced assertion about somebody else's product is what a referee may not make."""
    with pytest.raises(ValueError, match="claim_citation is required"):
        normalize_claim({"vendor_claims_pii_redaction": "not-offered"})
    with pytest.raises(ValueError, match="claim_citation is required"):
        normalize_claim({"vendor_claims_pii_redaction": "claimed"})


def test_enabled_redaction_must_name_what_was_configured():
    with pytest.raises(ValueError, match="configuration_reference is required"):
        normalize_claim(
            {
                "vendor_claims_pii_redaction": "claimed",
                "claim_citation": "https://example.invalid",
                "configured_for_this_run": True,
            }
        )


def test_cannot_configure_redaction_a_product_does_not_offer():
    with pytest.raises(ValueError, match="cannot be true"):
        normalize_claim(
            {
                "vendor_claims_pii_redaction": "not-offered",
                "claim_citation": "https://example.invalid",
                "configured_for_this_run": True,
            }
        )


def test_unknown_claim_keys_are_refused():
    with pytest.raises(ValueError, match="unknown keys"):
        normalize_claim({"vendor_claims_pii_redaction": "unknown", "outcome": "pass"})


def test_claim_is_not_a_free_string():
    with pytest.raises(ValueError, match="must be one of"):
        normalize_claim({"vendor_claims_pii_redaction": "sort-of"})


# ------------------------------------------------- an unattributable run is not a fail


def test_unattributable_run_is_inconclusive_not_a_failure():
    """captured=0 cannot separate 'never configured' from 'sent it elsewhere'."""
    claim = normalize_claim(dict(CLAIMED_AND_ON))
    assert (
        derive_outcome(claim, passed=False, attributable=False, leaked=True)
        == "inconclusive"
    )
    assert derive_outcome(claim, passed=False, attributable=True, leaked=True) == "fail"
    # And a non-pass with no leak evidence is never a leak verdict.
    assert (
        derive_outcome(claim, passed=False, attributable=True, leaked=False)
        == "no-leak-profile-not-met"
    )


# -------------------------------------------------------- the schema re-derives it


def test_schema_refuses_a_relabelled_failure(capture_port):
    """Controlled mutation: the dodge a submitter would actually attempt.

    Keep the claim at 'claimed' and configured, keep the leak, and simply write
    'not-applicable' over the outcome. The forward-only rule ("if the claim says
    not-offered then the outcome must be not-applicable") accepted exactly this. The
    reverse rule is what kills it. Reverting either direction reopens the dodge.
    """
    report = _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_ON)
    assert report["outcome"] == "fail"
    assert _errors(report) == []

    for dodge in ("not-applicable", "redaction-not-enabled", "claim-unstated", "inconclusive"):
        forged = copy.deepcopy(report)
        forged["outcome"] = dodge
        forged["outcome_rationale"] = "relabelled by hand"
        assert _errors(forged), f"schema accepted outcome={dodge} over a real failure"


def test_schema_refuses_a_verdict_without_a_stated_claim(capture_port):
    report = _run(capture_port, redaction_claim=CLAIMED_AND_ON)
    for claim_value in ("not-offered", "unknown"):
        forged = copy.deepcopy(report)
        forged["redaction_claim"]["vendor_claims_pii_redaction"] = claim_value
        assert _errors(forged), f"schema accepted outcome=pass with claim={claim_value}"


def test_schema_refuses_a_verdict_on_an_unattributable_run(capture_port):
    report = _run(capture_port, redaction_claim=CLAIMED_AND_ON)
    forged = copy.deepcopy(report)
    forged["checks"]["configured_upstream_boundary"]["correlated_requests"] = 0
    assert _errors(forged), "schema accepted a verdict with nothing correlated"


def test_schema_requires_the_claim_block_at_all(capture_port):
    report = _run(capture_port, redaction_claim=CLAIMED_AND_ON)
    for field in ("redaction_claim", "outcome", "outcome_rationale"):
        forged = copy.deepcopy(report)
        del forged[field]
        assert _errors(forged), f"schema accepted a report with no {field}"


# ------------------------------------ a product that leaked nothing is not a "Fail"


def _one_way_anonymizer(port):
    """Portkey's documented behaviour, and Presidio's replace/hash/mask.

    Replaces values with standardized identifiers and never restores them, so nothing
    egresses and the client cannot receive the original value back.
    """

    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            import urllib.request

            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
            prompt = payload["messages"][-1]["content"]
            # Values vary per run: recover them from the prompt by format.
            RAW = list(extract_fixture(prompt).values())
            anonymized = prompt
            for index, raw in enumerate(RAW):
                anonymized = anonymized.replace(raw, "{{ENTITY_%d}}" % index)
            payload["messages"][-1]["content"] = anonymized
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=json.dumps(payload).encode(),
                        headers={"content-type": "application/json"},
                    ),
                    timeout=30,
                ).read()
            except Exception:  # noqa: BLE001
                pass
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            # No rehydration: the client gets the identifiers, not the values.
            for character in anonymized:
                self.wfile.write(
                    b"data: "
                    + json.dumps({"choices": [{"delta": {"content": character}}]}).encode()
                    + b"\n\n"
                )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Gateway


def test_one_way_anonymizer_is_not_reported_as_a_leak(capture_port):
    """Portkey's PII Redaction replaces with {{EMAIL_ADDRESS_1}} and documents no
    rehydration, so a correctly configured Portkey run lands exactly here. Publishing
    it as "Fail" beside this project's "Pass" is the accusation that cannot be
    retracted -- and it was already recorded for Presidio.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _one_way_anonymizer(capture_port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        report = run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=capture_port,
            redaction_claim=CLAIMED_AND_ON,
        )
    finally:
        server.shutdown()
        server.server_close()

    boundary = report["checks"]["configured_upstream_boundary"]
    # Nothing egressed.
    assert boundary["leaked_entity_types"] == []
    assert boundary["passed"] is True
    # But it cannot rehydrate, so the profile's reversible-masking checks fail.
    assert report["checks"]["response_fidelity"]["passed"] is False
    assert report["passed"] is False

    # The outcome must say "no leak", not "fail".
    assert report["outcome"] == "no-leak-profile-not-met"
    assert "NOT A LEAK" in report["outcome_rationale"]
    assert _errors(report) == []


def test_schema_refuses_a_leak_verdict_without_leak_evidence(capture_port):
    """Controlled mutation: relabel the no-leak outcome as a leak.

    `fail` means one thing -- protected data reached the capture origin. Reverting the
    rule that requires leak evidence lets a non-leak be published as a privacy failure,
    which is the exact smear this outcome exists to prevent.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _one_way_anonymizer(capture_port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        report = run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=capture_port,
            redaction_claim=CLAIMED_AND_ON,
        )
    finally:
        server.shutdown()
        server.server_close()
    forged = copy.deepcopy(report)
    forged["outcome"] = "fail"
    forged["outcome_rationale"] = "relabelled by hand"
    assert _errors(forged), "schema published a leak verdict with no leak evidence"


def test_a_real_leak_still_reports_fail(capture_port):
    """The narrowing must not weaken the one outcome that matters."""
    report = _run(capture_port, redact=False, redaction_claim=CLAIMED_AND_ON)
    assert report["checks"]["configured_upstream_boundary"]["leaked_entity_types"]
    assert report["outcome"] == "fail"
    assert _errors(report) == []
