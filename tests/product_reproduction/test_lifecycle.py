from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from benchmarks.product_reproduction.catalog import RunnerRequirements, ServiceLimit
from benchmarks.product_reproduction.lifecycle import (
    EndpointOwnershipError,
    ExperimentHealth,
    LifecycleOptions,
    ProductLifecycleRunner,
    TargetExited,
)
from benchmarks.product_reproduction.resources import ResourceInsufficient, ResourceSnapshot
from benchmarks.product_reproduction.retry import RetryableAcquisitionError, RetryPolicy
from benchmarks.product_reproduction.sanitizer import SensitiveValue
from tests.product_reproduction.fake_adapter import FakeBehavior, FakeGatewayAdapter, FakeState

RUNNER = RunnerRequirements(
    runner_class="standard-ubuntu",
    minimum_memory_mib=1024,
    minimum_disk_mib=1024,
    service_limits=(ServiceLimit(service="gateway", memory_mib=512),),
)
SUFFICIENT = ResourceSnapshot(available_memory_mib=4096, available_disk_mib=8192, docker_available=True)


def _run(
    tmp_path: Path,
    *,
    state: FakeState | None = None,
    behavior: FakeBehavior | None = None,
    snapshot: ResourceSnapshot = SUFFICIENT,
    retry_policy: RetryPolicy | None = None,
):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    output = tmp_path / "outside" / "run"
    shared_state = state or FakeState()
    selected_behavior = behavior or FakeBehavior()
    runner = ProductLifecycleRunner(
        adapter_factory=lambda: FakeGatewayAdapter(shared_state, selected_behavior),
        resource_probe=lambda _: snapshot,
    )
    result = runner.run(
        target_id="test-gateway-default",
        runner_requirements=RUNNER,
        output_dir=output,
        repo_root=repo,
        sensitive_values=[SensitiveValue(label="FIXTURE_EMAIL", value="alice.fixture@example.test")],
        options=LifecycleOptions(retry_policy=retry_policy or RetryPolicy(max_attempts=3)),
    )
    return result, shared_state, output


def test_capture_starts_before_each_fresh_gateway_and_cleanup_is_scoped(tmp_path: Path) -> None:
    result, state, output = _run(tmp_path)

    assert result.health is ExperimentHealth.COMPLETE
    for profile in ("operator", "response-midpoint"):
        capture_index = state.events.index(("capture-ready", profile))
        start_index = state.events.index(("start", profile))
        stop_index = state.events.index(("stop", profile))
        assert capture_index < start_index < stop_index
    assert len(state.adapter_instance_ids) == 3  # acquisition plus one fresh adapter per profile
    assert len(set(state.started_ports)) == 2
    assert state.owned_resources == set()
    assert output.is_dir()
    assert not any(path.name.startswith("product-reproduction-work-") for path in output.parent.iterdir())


def test_two_simultaneous_runs_receive_distinct_ports_and_suffixes(tmp_path: Path) -> None:
    state = FakeState()

    def one(name: str):
        local = tmp_path / name
        local.mkdir()
        return _run(local, state=state)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, ("one", "two")))

    assert all(result.health is ExperimentHealth.COMPLETE for result in results)
    assert len(state.started_ports) == 4
    assert len(state.run_suffixes) == len(set(state.run_suffixes)) == 2


def test_resource_preflight_prevents_adapter_creation_and_start(tmp_path: Path) -> None:
    state = FakeState()
    result, _, _ = _run(
        tmp_path,
        state=state,
        snapshot=ResourceSnapshot(available_memory_mib=512, available_disk_mib=8192, docker_available=True),
    )

    assert result.health is ExperimentHealth.RESOURCE_INSUFFICIENT
    assert state.adapter_instance_ids == []
    assert not any(event[0] == "start" for event in state.events)


def test_adapter_resource_validation_preserves_resource_insufficient_state(tmp_path: Path) -> None:
    result, state, _ = _run(
        tmp_path,
        behavior=FakeBehavior(validate_error=ResourceInsufficient("adapter cgroup memory is too low")),
    )

    assert result.health is ExperimentHealth.RESOURCE_INSUFFICIENT
    assert not any(event[0] == "start" for event in state.events)


def test_acquisition_retries_but_scored_requests_do_not(tmp_path: Path) -> None:
    state = FakeState()
    behavior = FakeBehavior(acquire_failures=2)
    result, _, _ = _run(
        tmp_path,
        state=state,
        behavior=behavior,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0),
    )

    assert result.health is ExperimentHealth.COMPLETE
    assert state.acquire_calls == 3

    failing_state = FakeState()
    failing = FakeBehavior(operator_error=RetryableAcquisitionError("do not retry", category="scored"))
    result, _, _ = _run(tmp_path / "scored", state=failing_state, behavior=failing)
    assert result.health is ExperimentHealth.INFRASTRUCTURE_ERROR
    assert failing_state.operator_calls == 1


def test_exit_two_is_not_measured_but_exit_one_is_complete(tmp_path: Path) -> None:
    measured, _, _ = _run(tmp_path / "leak", behavior=FakeBehavior(operator_exit=1, response_exit=1))
    not_measured, _, _ = _run(tmp_path / "invalid", behavior=FakeBehavior(operator_exit=2))

    assert measured.health is ExperimentHealth.COMPLETE
    assert measured.exit_codes == {"operator": 1, "response-midpoint": 1}
    assert not_measured.health is ExperimentHealth.NOT_MEASURED
    assert not_measured.exit_codes["operator"] == 2


def test_oom_exit_is_resource_insufficient_and_diagnostics_are_sanitized(tmp_path: Path) -> None:
    fixture = "alice.fixture@example.test"
    behavior = FakeBehavior(
        start_error=TargetExited(137),
        diagnostic_message=f"target echoed {fixture}",
    )

    result, state, _ = _run(tmp_path, behavior=behavior)

    assert result.health is ExperimentHealth.RESOURCE_INSUFFICIENT
    assert fixture not in " ".join(result.diagnostics)
    assert "<FIXTURE_EMAIL>" in " ".join(result.diagnostics)
    assert any(event[0] == "diagnostics" for event in state.events)
    assert any(event[0] == "stop" for event in state.events)


def test_identity_mismatch_does_not_stop_unrelated_listener(tmp_path: Path) -> None:
    unrelated = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unrelated.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    unrelated.bind(("127.0.0.1", 0))
    unrelated.listen()
    port = unrelated.getsockname()[1]
    state = FakeState()
    try:
        result, _, _ = _run(
            tmp_path,
            state=state,
            behavior=FakeBehavior(identity_error=EndpointOwnershipError("wrong owner")),
        )
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        unrelated.close()

    assert result.health is ExperimentHealth.NOT_MEASURED
    assert state.owned_resources == set()


def test_exception_path_collects_diagnostics_and_removes_workspace(tmp_path: Path) -> None:
    state = FakeState()
    behavior = FakeBehavior(wait_error=TimeoutError("readiness deadline"))
    result, _, output = _run(tmp_path, state=state, behavior=behavior)

    assert result.health is ExperimentHealth.INFRASTRUCTURE_ERROR
    assert any(event[0] == "diagnostics" for event in state.events)
    assert any(event[0] == "stop" for event in state.events)
    assert not any(path.name.startswith("product-reproduction-work-") for path in output.parent.iterdir())


def test_keyboard_interrupt_still_collects_diagnostics_and_cleans_up(tmp_path: Path) -> None:
    state = FakeState()
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, state=state, behavior=FakeBehavior(wait_error=KeyboardInterrupt()))

    assert any(event[0] == "diagnostics" for event in state.events)
    assert any(event[0] == "stop" for event in state.events)
    assert state.owned_resources == set()
    outside = tmp_path / "outside"
    assert not any(path.name.startswith("product-reproduction-work-") for path in outside.iterdir())


def test_readiness_deadline_is_present_before_gateway_start(tmp_path: Path) -> None:
    state = FakeState(lock=threading.Lock())
    result, _, _ = _run(tmp_path, state=state)

    assert result.health is ExperimentHealth.COMPLETE
    assert state.readiness_deadlines
    assert all(deadline > 0 for deadline in state.readiness_deadlines)
