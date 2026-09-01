"""The report envelope must reject reports the harness could never legitimately emit.

Before these existed nothing in the repo validated any report against the published
schema, so `passed: true` alongside seven failed checks was a conforming document.
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "spec" / "v1.0.0"
REPORT_SCHEMA = json.loads((SPEC_DIR / "report.schema.json").read_text(encoding="utf-8"))
HTTP_SCHEMA = json.loads((SPEC_DIR / "http-profile.schema.json").read_text(encoding="utf-8"))

CHECK_NAMES = [
    "fragmentation_safety",
    "raw_pii_egress",
    "sse_validity",
    "rehydration_fidelity",
    "audit_integrity",
    "memory_bounded",
]


def _errors(schema, document):
    return list(Draft202012Validator(schema).iter_errors(document))


def test_published_schemas_are_themselves_valid():
    Draft202012Validator.check_schema(REPORT_SCHEMA)
    Draft202012Validator.check_schema(HTTP_SCHEMA)


@pytest.fixture(scope="module")
def local_report():
    from llm_shield_proxy.conformance import run_conformance

    return run_conformance(20)


def test_emitted_local_report_validates(local_report):
    assert _errors(REPORT_SCHEMA, local_report) == []
    assert local_report["passed"] is True


@pytest.mark.parametrize("check_name", CHECK_NAMES)
def test_passed_true_is_rejected_when_any_check_failed(local_report, check_name):
    forged = copy.deepcopy(local_report)
    forged["checks"][check_name]["passed"] = False
    forged["passed"] = True
    assert _errors(REPORT_SCHEMA, forged), f"{check_name} may not fail while passed is true"


def test_egress_pass_is_rejected_when_entities_leaked(local_report):
    forged = copy.deepcopy(local_report)
    forged["checks"]["raw_pii_egress"] = {"passed": True, "leaked_entity_types": ["EMAIL"]}
    assert _errors(REPORT_SCHEMA, forged)


def test_local_report_cannot_relabel_the_measured_egress_fixture(local_report):
    """A valid envelope must not claim a different boundary or entity fixture."""
    forged = copy.deepcopy(local_report)
    egress = forged["checks"]["raw_pii_egress"]
    egress["boundary"] = "post-model response and browser storage"
    egress["protected_entity_types"] = ["PHONE"]
    assert _errors(REPORT_SCHEMA, forged), "schema accepted a different measured boundary"


def test_evidence_free_envelope_is_rejected(local_report):
    for field, value in (("limitations", []), ("environment", {})):
        forged = copy.deepcopy(local_report)
        forged[field] = value
        assert _errors(REPORT_SCHEMA, forged), f"empty {field} must be rejected"


def test_attestation_must_declare_its_verification_basis(local_report):
    document = copy.deepcopy(local_report)
    document["attestation"] = {
        "verification": "self-reported",
        "runner": "gh-hosted",
        "commit_sha": "7e959d9",
    }
    assert _errors(REPORT_SCHEMA, document) == []

    document["attestation"]["verification"] = "trust-me"
    assert _errors(REPORT_SCHEMA, document)


@pytest.mark.parametrize("verification", ["github-oidc", "sigstore"])
def test_attestation_cannot_claim_an_unimplemented_verification_mechanism(
    local_report, verification
):
    """No proof field exists yet, so these labels would be an unsupported claim."""
    document = copy.deepcopy(local_report)
    document["attestation"] = {
        "verification": verification,
        "runner": "self-asserted-runner",
        "commit_sha": "7e959d9",
    }
    assert _errors(REPORT_SCHEMA, document)


def test_passing_local_report_requires_check_measurements(local_report):
    """Bare true flags plus decorative top-level numbers are not measurements."""
    hollow = copy.deepcopy(local_report)
    hollow["checks"] = {name: {"passed": True} for name in CHECK_NAMES}
    hollow["microbenchmarks"] = {
        "iterations": 1,
        "scope": "claimed measurement",
        "unit": "nanoseconds",
    }
    hollow["memory"] = {
        "scope": "claimed measurement",
        "iterations": 1,
        "retained_characters": 0,
        "retention_bound_characters": 0,
    }
    hollow["passed"] = True
    assert _errors(REPORT_SCHEMA, hollow)


@pytest.mark.parametrize("field", ["iterations", "scope", "unit"])
def test_each_microbenchmark_basis_field_is_required(local_report, field):
    document = copy.deepcopy(local_report)
    del document["microbenchmarks"][field]
    assert _errors(REPORT_SCHEMA, document)


def test_committed_artifacts_validate():
    results = REPO_ROOT / "benchmarks" / "results"
    for path in sorted(results.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        schema = HTTP_SCHEMA if "http-profile" in document.get("schema", "") else REPORT_SCHEMA
        assert _errors(schema, document) == [], f"{path.name} does not validate"


def test_measurement_evidence_cannot_be_stripped(local_report):
    """`passed: true` with no numbers behind it is not a conformance result.

    An envelope that required `microbenchmarks` and `memory` to be objects, but
    nothing about their contents, accepted `{}` for both alongside seven passing
    checks stripped down to a bare `passed` flag.
    """
    for field in ("microbenchmarks", "memory"):
        forged = copy.deepcopy(local_report)
        forged[field] = {}
        assert _errors(REPORT_SCHEMA, forged), f"empty {field} must be rejected"

    hollow = copy.deepcopy(local_report)
    hollow["microbenchmarks"] = {}
    hollow["memory"] = {}
    for check in hollow["checks"].values():
        for key in [k for k in check if k != "passed"]:
            del check[key]
    assert _errors(REPORT_SCHEMA, hollow)


def test_boundary_counts_must_be_internally_consistent():
    """A request cannot be correlated to this run without having been captured."""
    results = REPO_ROOT / "benchmarks" / "results"
    source = next(p for p in results.glob("*.json") if "http-profile" in p.name)
    document = json.loads(source.read_text(encoding="utf-8"))

    forged = copy.deepcopy(document)
    boundary = forged["checks"]["configured_upstream_boundary"]
    boundary["passed"] = True
    boundary["captured_requests"] = 0
    boundary["correlated_requests"] = 1
    boundary["uninspectable_requests"] = 0
    boundary["leaked_entity_types"] = []
    forged["passed"] = True
    assert _errors(HTTP_SCHEMA, forged), "0 captures cannot yield 1 correlated request"


def test_boundary_pass_requires_no_leak_and_no_blind_spot():
    results = REPO_ROOT / "benchmarks" / "results"
    source = next(p for p in results.glob("*.json") if "http-profile" in p.name)
    document = json.loads(source.read_text(encoding="utf-8"))

    for field, value in (("leaked_entity_types", ["SSN"]), ("uninspectable_requests", 1)):
        forged = copy.deepcopy(document)
        boundary = forged["checks"]["configured_upstream_boundary"]
        boundary.update(
            {"passed": True, "captured_requests": 3, "correlated_requests": 3,
             "uninspectable_requests": 0, "leaked_entity_types": []}
        )
        boundary[field] = value
        forged["passed"] = True
        assert _errors(HTTP_SCHEMA, forged), f"boundary passed with {field}={value}"


def test_http_report_cannot_expand_the_profile_scope_beyond_capture_origin():
    source = REPO_ROOT / "benchmarks" / "results" / "http-profile-llm-shield-proxy-working-tree.json"
    forged = json.loads(source.read_text(encoding="utf-8"))
    assert forged["passed"] is True
    forged["profile"]["scope"] = "HTTP/2, DNS, TLS, and egress to every destination"
    forged["checks"]["configured_upstream_boundary"]["inspection_scope"] = (
        "all protocols and all destinations"
    )
    assert _errors(HTTP_SCHEMA, forged), "schema accepted an observation-scope overclaim"


def test_passing_sse_check_requires_success_status():
    source = REPO_ROOT / "benchmarks" / "results" / "http-profile-llm-shield-proxy-working-tree.json"
    forged = json.loads(source.read_text(encoding="utf-8"))
    assert forged["passed"] is True
    sse = forged["checks"]["sse_validity"]
    sse["status_codes"] = [500]
    assert _errors(HTTP_SCHEMA, forged), "schema accepted a passing HTTP 500"


def test_emitted_http_report_validates():
    from llm_shield_proxy.conformance.http_profile import run_http_conformance

    report = run_http_conformance("capture://self", iterations=1, capture_port=8793)
    assert _errors(HTTP_SCHEMA, report) == []
    assert report["passed"] is False


def test_passing_http_report_requires_per_check_evidence():
    from llm_shield_proxy.conformance.http_profile import run_http_conformance

    hollow = run_http_conformance("capture://self", iterations=1, capture_port=8794)
    hollow["checks"] = {
        "configured_upstream_boundary": {
            "passed": True,
            "captured_requests": 1,
            "correlated_requests": 1,
            "uninspectable_requests": 0,
            "leaked_entity_types": [],
        },
        "fragmentation_safety": {"passed": True},
        "sse_validity": {"passed": True},
        "response_fidelity": {"passed": True},
        "client_observed_latency": {"passed": True},
    }
    hollow["passed"] = True
    assert _errors(HTTP_SCHEMA, hollow)
