"""`spec/v2.0.0` must reject the reports a corpus run could never legitimately emit.

v2 adds one measurement v1 does not have, and everything else here follows from it.

v1 measures leakage in ONE direction: values the client sent, recovered at the
configured-upstream capture. Transport fragmentation cannot be measured there. The
harness does not choose where a gateway chunks its own upstream request, and a gateway
that buffers and JSON-parses a body -- nearly all of them -- has no chunk boundaries to
place. All the control the harness has is on the RESPONSE path, because the capture
server is the gateway's configured upstream and is the harness's own code.

So v2 splits the response into two segments, in separate bytes:

    ECHO       replays the prompt. A masking gateway must rehydrate it. FidelityRate.
    INJECTION  fixture values that were never in the prompt, emitted by the capture as
               a model would emit them, fragmented per the Axis C strategy under test.
               A gateway must redact them. LeakRate, and DeltaFrag from its two arms.

The property that makes this hard to game: a correct gateway must behave DIFFERENTLY
on the two segments. Pass everything through and injection containment fails; redact
everything and echo fidelity fails. No single global policy satisfies both -- which is
precisely what v1's three-format fixture could not force.

These tests assert the schema is SATISFIABLE (an exemplar validates) and that each new
rule rejects the thing it claims to reject. A schema nothing is tested against is a
claim, not a constraint -- which is the state this repo was in before
`test_spec_schema.py` existed.

The exemplar is constructed here rather than committed under `benchmarks/results`. It
is a synthetic document that describes a run nobody performed, and a fabricated
measurement does not belong in the results directory whatever its filename says.
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
V1_DIR = REPO_ROOT / "spec" / "v1.0.0"
V2_DIR = REPO_ROOT / "spec" / "v2.0.0"
V1_SCHEMA = json.loads((V1_DIR / "http-profile.schema.json").read_text(encoding="utf-8"))
V2_SCHEMA = json.loads((V2_DIR / "http-profile.schema.json").read_text(encoding="utf-8"))

V2_INSPECTION_SCOPE = V2_SCHEMA["$defs"]["boundary_check"]["properties"][
    "inspection_scope"
]["enum"][0]
V2_CLIENT_SCOPE = V2_SCHEMA["$defs"]["injection_check"]["properties"][
    "inspection_scope"
]["enum"][0]


def _errors(schema, document):
    return list(Draft202012Validator(schema).iter_errors(document))


def _v2_exemplar():
    """A passing v2 report, grown from the committed v1 artifact.

    Built from a real v1 report rather than typed from scratch so the v1 blocks are
    exactly the ones the harness emits: a hand-written envelope would drift from the
    producer and the tests would pass against a document nothing can produce.
    """
    source = REPO_ROOT / "benchmarks" / "results" / "http-profile-llm-shield-proxy-working-tree.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    assert document["passed"] is True, "the exemplar must start from a passing run"

    document["schema"] = "llm-shield.streaming-privacy-http-profile/v2.0.0"
    document["checks"]["configured_upstream_boundary"]["inspection_scope"] = V2_INSPECTION_SCOPE
    document["checks"]["response_fidelity"]["segment"] = "echo"
    document["checks"]["response_injection_containment"] = {
        "passed": True,
        "segment": "injection",
        "fragmentation_strategy": "placeholder-boundary",
        "injected_entity_types": ["SSN", "IBAN"],
        "leaked_entity_types": [],
        "leak_evidence": [],
        "needle_proximity": {"SSN": 2, "IBAN": 3},
        "needle_lengths": {"SSN": 9, "IBAN": 22},
        "chunk_boundaries_requested": 8,
        "chunk_boundaries_observed": 8,
        "events_observed": 96,
        "delivery_confirmed": True,
        "client_capture_inspectable": True,
        "inspection_scope": V2_CLIENT_SCOPE,
        "payload_content_included": False,
    }
    document["checks"]["segment_separation"] = {
        "passed": True,
        # Registry ids, not v1's report labels. v1 emitted `CREDIT_CARD`, which is 11
        # characters and is REJECTED here -- see the entity-id length budget test
        # below. The registry id is `CARDPAN`. That rename is the one migration a v2
        # producer cannot skip.
        "echo_entity_types": ["CARDPAN", "EMAIL", "SSN"],
        "injection_entity_types": ["IBAN", "SSN"],
        "values_disjoint": True,
        "normalized_forms_disjoint": True,
        "injection_absent_from_request": True,
        "shared_substring_max": 3,
        "shortest_needle_length": 9,
    }
    document["corpus"] = {
        "id": "pii-leak-corpus",
        "version": "1.0.0",
        "sha256": "a" * 64,
        "case_count": 500,
        "seed": "0123456789abcdef",
        "coverage": {
            "strategy": "pairwise-plus-adversarial",
            "axes": ["entity", "encoding", "fragmentation", "carrier"],
            "pairs_required": 1_184,
            "pairs_covered": 1_184,
            "proof_complete": True,
        },
        "values_published": False,
    }
    document["metrics"] = {
        "leak_rate": {"single_chunk": 0.0, "adversarial": 0.0, "overall": 0.0},
        "fidelity_rate": 1.0,
        "delta_frag": 0.0,
        "cases_by_condition": {"single_chunk": 120, "adversarial": 380},
        "cases_scored": 500,
        "cases_applicable": 500,
        "cases_inconclusive": 0,
        "derivation_recomputed": True,
        "sidecar_case_count_matches": True,
        "by_axis": {
            "entity": {"SSN": {"leak_rate": 0.0, "applicable": 40, "leaked": 0}},
            "encoding": {"base64": {"leak_rate": 0.0, "applicable": 50, "leaked": 0}},
            "fragmentation": {
                "placeholder-boundary": {"leak_rate": 0.0, "applicable": 60, "leaked": 0}
            },
            "carrier": {
                "message-content": {"leak_rate": 0.0, "applicable": 90, "leaked": 0}
            },
        },
    }
    document["entity_scope"] = {
        "mechanism": "region-profile",
        "enabled": ["SSN", "EMAIL", "CARDPAN"],
        "not_enabled": ["IBAN", "AADHAAR"],
        "unknown": [],
        "partitions_corpus": True,
        "recorded_by": "operator",
        "source": "config/policies.yaml, region profile 'us'",
    }
    document["cases_digest"] = "b" * 64
    return document


@pytest.fixture
def report():
    return _v2_exemplar()


def test_the_published_v2_schema_is_itself_valid():
    Draft202012Validator.check_schema(V2_SCHEMA)


def test_v1_stays_frozen_and_v2_is_a_separate_document():
    """Published artifacts pin v1 and cannot be re-run, so v1 is not edited in place.

    The one thing that would silently break that is v2 reusing v1's `$id` or schema
    string, because a consumer resolving either would then get whichever file it
    happened to fetch.
    """
    assert V1_SCHEMA["$id"] != V2_SCHEMA["$id"]
    assert V1_SCHEMA["properties"]["schema"]["const"].endswith("/v1.0.0")
    assert V2_SCHEMA["properties"]["schema"]["const"].endswith("/v2.0.0")


def test_the_exemplar_validates(report):
    assert _errors(V2_SCHEMA, report) == []


def test_a_v1_report_is_not_a_v2_report(report):
    """v2 is a breaking change and must say so by rejecting, not by silently
    accepting a document that is missing every corpus block."""
    source = REPO_ROOT / "benchmarks" / "results" / "http-profile-llm-shield-proxy-working-tree.json"
    v1_document = json.loads(source.read_text(encoding="utf-8"))
    assert _errors(V1_SCHEMA, v1_document) == []
    assert _errors(V2_SCHEMA, v1_document)


@pytest.mark.parametrize(
    "block", ["corpus", "metrics", "cases_digest", "entity_scope"]
)
def test_every_new_top_level_block_is_required(report, block):
    forged = copy.deepcopy(report)
    del forged[block]
    assert _errors(V2_SCHEMA, forged), f"{block} may not be optional"


@pytest.mark.parametrize(
    "check", ["response_injection_containment", "segment_separation"]
)
def test_a_failed_new_check_forbids_an_overall_pass(report, check):
    forged = copy.deepcopy(report)
    forged["checks"][check]["passed"] = False
    assert _errors(V2_SCHEMA, forged), f"{check} failed while the run passed"


def test_a_leaked_injection_cannot_report_containment(report):
    forged = copy.deepcopy(report)
    forged["checks"]["response_injection_containment"]["leaked_entity_types"] = ["SSN"]
    assert _errors(V2_SCHEMA, forged)


def test_an_undelivered_injection_is_not_a_pass(report):
    """A gateway that dropped the stream leaks nothing and has been measured for
    nothing. Without this the two are byte-identical in the report."""
    forged = copy.deepcopy(report)
    forged["checks"]["response_injection_containment"]["delivery_confirmed"] = False
    assert _errors(V2_SCHEMA, forged)


def test_an_uninspectable_client_capture_is_not_a_pass(report):
    forged = copy.deepcopy(report)
    forged["checks"]["response_injection_containment"]["client_capture_inspectable"] = False
    assert _errors(V2_SCHEMA, forged)


def test_an_empty_injection_is_not_a_measurement(report):
    forged = copy.deepcopy(report)
    forged["checks"]["response_injection_containment"]["injected_entity_types"] = []
    assert _errors(V2_SCHEMA, forged), "injecting nothing and leaking nothing is not a pass"


@pytest.mark.parametrize(
    "field",
    ["values_disjoint", "normalized_forms_disjoint", "injection_absent_from_request"],
)
def test_segment_separation_cannot_pass_without_its_proof(report, field):
    """If the segments shared a value, a correct rehydration of the echo would look
    exactly like a leak of the injection -- the harness penalising correct behaviour,
    which is the invalid-fixture incident in a new costume."""
    forged = copy.deepcopy(report)
    forged["checks"]["segment_separation"][field] = False
    assert _errors(V2_SCHEMA, forged)


def test_any_measured_leak_forbids_a_pass(report):
    forged = copy.deepcopy(report)
    forged["metrics"]["leak_rate"]["overall"] = 0.002
    assert _errors(V2_SCHEMA, forged), "a leaked case may not round away into a pass"


def test_incomplete_fidelity_forbids_a_pass(report):
    forged = copy.deepcopy(report)
    forged["metrics"]["fidelity_rate"] = 0.98
    assert _errors(V2_SCHEMA, forged)


def test_inconclusive_cases_forbid_a_pass(report):
    """v1's fail-closed rule, aggregated: an uninspectable capture is never an assumed
    no-leak."""
    forged = copy.deepcopy(report)
    forged["metrics"]["cases_inconclusive"] = 1
    assert _errors(V2_SCHEMA, forged)


def test_an_unclosed_coverage_proof_forbids_a_pass(report):
    forged = copy.deepcopy(report)
    forged["corpus"]["coverage"]["proof_complete"] = False
    assert _errors(V2_SCHEMA, forged)


@pytest.mark.parametrize("condition", ["single_chunk", "adversarial"])
def test_delta_frag_requires_both_arms(report, condition):
    """A delta computed without a control condition is not a delta."""
    forged = copy.deepcopy(report)
    forged["metrics"]["cases_by_condition"][condition] = 0
    assert _errors(V2_SCHEMA, forged)


@pytest.mark.parametrize(
    "field", ["derivation_recomputed", "sidecar_case_count_matches"]
)
def test_the_harness_asserted_consistency_flags_cannot_be_false(report, field):
    """JSON Schema cannot do arithmetic, so these are harness assertions. The schema's
    job here is to make omitting or negating them impossible -- not to recompute them,
    and not to defend against a hand-forged report. Same trust model as v1's
    `payload_content_included`, and it is written down rather than implied."""
    for value in (False, None):
        forged = copy.deepcopy(report)
        forged["metrics"][field] = value
        assert _errors(V2_SCHEMA, forged)


def test_the_entity_id_length_budget_is_enforced(report):
    """Ten ASCII characters, and it is a streaming constraint rather than a style rule.

    The vault's look-behind retention is `L = N - 1` characters where `N` is the
    maximum placeholder length, and the placeholder is derived from the entity id.
    Every character added to the LONGEST id widens the window the SSE rehydration
    buffer holds on the hot path, for every request, whether or not that entity is
    ever seen. v1's own `CREDIT_CARD` label is 11 characters and does not fit, which
    is why the registry id is `CARDPAN`.
    """
    forged = copy.deepcopy(report)
    forged["checks"]["segment_separation"]["echo_entity_types"] = ["CREDIT_CARD"]
    assert _errors(V2_SCHEMA, forged), "an 11-character entity id must be rejected"


def test_entity_scope_must_partition_the_corpus(report):
    forged = copy.deepcopy(report)
    forged["entity_scope"]["partitions_corpus"] = False
    assert _errors(V2_SCHEMA, forged)


def test_entity_scope_is_where_an_unearned_pass_would_hide(report):
    """A gateway that never had an entity class enabled has not passed that class; it
    was not tested. The scope block is required so a row cannot omit that fact -- this
    asserts the distinction survives, not that any particular entity was disabled."""
    assert set(report["entity_scope"]["not_enabled"]) & set(
        report["checks"]["response_injection_containment"]["injected_entity_types"]
    ), "the exemplar should exercise an entity the gateway did not have enabled"


def test_fail_may_be_derived_from_a_response_path_leak(report):
    """'fail' is still reserved for a MEASURED leak. v2 adds a second place one can be
    measured, and a response-path leak must be able to derive it without a
    request-path leak also being present."""
    forged = copy.deepcopy(report)
    forged["passed"] = False
    forged["outcome"] = "fail"
    forged["checks"]["response_injection_containment"]["passed"] = False
    forged["checks"]["response_injection_containment"]["leaked_entity_types"] = ["SSN"]
    forged["metrics"]["leak_rate"] = {
        "single_chunk": 0.0,
        "adversarial": 0.12,
        "overall": 0.09,
    }
    forged["metrics"]["delta_frag"] = 0.12
    assert _errors(V2_SCHEMA, forged) == []


def test_fail_still_requires_evidence_from_somewhere(report):
    forged = copy.deepcopy(report)
    forged["passed"] = False
    forged["outcome"] = "fail"
    assert _errors(V2_SCHEMA, forged), "'fail' with no leaked entity anywhere"


def test_a_response_path_leak_cannot_be_published_as_no_leak(report):
    """Without this rule a leak measured on the response path could be relabelled as
    the outcome reserved for runs that leaked nothing."""
    forged = copy.deepcopy(report)
    forged["passed"] = False
    forged["outcome"] = "no-leak-profile-not-met"
    forged["checks"]["response_injection_containment"]["passed"] = False
    forged["checks"]["response_injection_containment"]["leaked_entity_types"] = ["SSN"]
    assert _errors(V2_SCHEMA, forged)
