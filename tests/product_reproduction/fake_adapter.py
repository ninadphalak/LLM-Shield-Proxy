from __future__ import annotations

import itertools
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.product_reproduction.adapters.base import (
    ArtifactIdentity,
    CaptureEndpoint,
    Diagnostic,
    ProductAdapter,
    Profile,
    RunContext,
)
from benchmarks.product_reproduction.lifecycle import EndpointOwnershipError
from benchmarks.product_reproduction.retry import RetryableAcquisitionError


@dataclass
class FakeBehavior:
    acquire_failures: int = 0
    operator_exit: int = 0
    response_exit: int = 0
    validate_error: BaseException | None = None
    start_error: BaseException | None = None
    wait_error: BaseException | None = None
    identity_error: EndpointOwnershipError | None = None
    operator_error: BaseException | None = None
    response_error: BaseException | None = None
    diagnostic_message: str = "fake adapter healthy"


@dataclass
class FakeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    events: list[tuple[str, str]] = field(default_factory=list)
    adapter_instance_ids: list[int] = field(default_factory=list)
    started_ports: list[int] = field(default_factory=list)
    run_suffixes: set[str] = field(default_factory=set)
    readiness_deadlines: list[float] = field(default_factory=list)
    owned_resources: set[str] = field(default_factory=set)
    acquire_calls: int = 0
    operator_calls: int = 0
    response_calls: int = 0
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def record(self, event: str, profile: Profile | None) -> None:
        with self.lock:
            self.events.append((event, profile or "none"))


class FakeGatewayAdapter(ProductAdapter):
    """Deterministic test double; deliberately absent from the production registry."""

    def __init__(self, state: FakeState | None = None, behavior: FakeBehavior | None = None) -> None:
        self.state = state or FakeState()
        self.behavior = behavior or FakeBehavior()
        self.events = self.state.events
        with self.state.lock:
            self.instance_id = next(self.state._ids)
            self.state.adapter_instance_ids.append(self.instance_id)

    def validate_host(self, context: RunContext) -> None:
        self.state.record("validate_host", context.profile)
        with self.state.lock:
            self.state.run_suffixes.add(context.run_suffix)
        if self.behavior.validate_error is not None:
            raise self.behavior.validate_error

    def acquire(self, context: RunContext) -> ArtifactIdentity:
        self.state.record("acquire", context.profile)
        with self.state.lock:
            self.state.acquire_calls += 1
            should_fail = self.state.acquire_calls <= self.behavior.acquire_failures
        if should_fail:
            raise RetryableAcquisitionError("temporary fake acquisition failure", category="fake-acquire")
        return ArtifactIdentity(reference="test==1.0.0", identity="sha256:" + "1" * 64, version="1.0.0")

    def render_config(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> Path:
        self.state.record("render_config", profile)
        return context.output_dir / "rendered.json"

    def start(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> None:
        with socket.create_connection((capture.host, capture.port), timeout=1):
            pass
        self.state.record("capture-ready", profile)
        self.state.record("start", profile)
        resource = f"{context.run_suffix}:{profile}:{self.instance_id}"
        with self.state.lock:
            self.state.started_ports.append(capture.port)
            self.state.readiness_deadlines.append(context.readiness_deadline_monotonic)
            self.state.owned_resources.add(resource)
        if self.behavior.start_error is not None:
            raise self.behavior.start_error

    def wait_ready(self, context: RunContext) -> None:
        self.state.record("wait_ready", context.profile)
        if self.behavior.wait_error is not None:
            raise self.behavior.wait_error

    def assert_identity(self, context: RunContext, artifact: ArtifactIdentity) -> None:
        self.state.record("assert_identity", context.profile)
        if self.behavior.identity_error is not None:
            raise self.behavior.identity_error

    def measure_operator(self, context: RunContext) -> int:
        self.state.record("measure_operator", context.profile)
        with self.state.lock:
            self.state.operator_calls += 1
        if self.behavior.operator_error is not None:
            raise self.behavior.operator_error
        return self.behavior.operator_exit

    def measure_response_midpoint(self, context: RunContext) -> int:
        self.state.record("measure_response_midpoint", context.profile)
        with self.state.lock:
            self.state.response_calls += 1
        if self.behavior.response_error is not None:
            raise self.behavior.response_error
        return self.behavior.response_exit

    def collect_diagnostics(self, context: RunContext) -> tuple[Diagnostic, ...]:
        self.state.record("diagnostics", context.profile)
        return (Diagnostic(category="fake", message=self.behavior.diagnostic_message),)

    def stop(self, context: RunContext) -> None:
        self.state.record("stop", context.profile)
        prefix = f"{context.run_suffix}:{context.profile}:"
        with self.state.lock:
            self.state.owned_resources = {
                resource for resource in self.state.owned_resources if not resource.startswith(prefix)
            }
