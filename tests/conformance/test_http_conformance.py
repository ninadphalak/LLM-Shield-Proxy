import json
from pathlib import Path

from llm_shield_proxy.conformance import write_conformance_report
from llm_shield_proxy.conformance.http_profile import run_http_conformance


def test_http_profile_can_measure_and_fail_a_raw_openai_compatible_endpoint(tmp_path):
    report = run_http_conformance(
        "capture://self",
        implementation_name="raw-capture-baseline",
        implementation_version="test",
        iterations=1,
        capture_port=0,
    )

    assert report["passed"] is False
    assert report["checks"]["configured_upstream_boundary"]["passed"] is False
    assert report["checks"]["configured_upstream_boundary"]["captured_requests"] == 1
    assert report["checks"]["fragmentation_safety"]["passed"] is True
    assert report["checks"]["sse_validity"]["passed"] is True
    assert report["checks"]["response_fidelity"]["passed"] is True

    destination = write_conformance_report(report, str(tmp_path / "http-report.json"))
    serialized = Path(destination).read_text(encoding="utf-8")
    assert "person@example.invalid" not in serialized
    assert "123-45-6789" not in serialized
    assert json.loads(serialized)["implementation"]["name"] == "raw-capture-baseline"
