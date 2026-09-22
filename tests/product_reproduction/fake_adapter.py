from __future__ import annotations

from pathlib import Path

from benchmarks.product_reproduction.adapters.base import (
    ArtifactIdentity,
    CaptureEndpoint,
    Diagnostic,
    ProductAdapter,
    Profile,
    RunContext,
)


class FakeGatewayAdapter(ProductAdapter):
    """Deterministic test double; deliberately absent from the production registry."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def validate_host(self, context: RunContext) -> None:
        self.events.append("validate_host")

    def acquire(self, context: RunContext) -> ArtifactIdentity:
        self.events.append("acquire")
        return ArtifactIdentity(reference="test==1.0.0", identity="sha256:" + "1" * 64, version="1.0.0")

    def render_config(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> Path:
        self.events.append("render_config")
        return context.output_dir / "rendered.json"

    def start(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> None:
        self.events.append("start")

    def wait_ready(self, context: RunContext) -> None:
        self.events.append("wait_ready")

    def assert_identity(self, context: RunContext, artifact: ArtifactIdentity) -> None:
        self.events.append("assert_identity")

    def measure_operator(self, context: RunContext) -> int:
        self.events.append("measure_operator")
        return 0

    def measure_response_midpoint(self, context: RunContext) -> int:
        self.events.append("measure_response_midpoint")
        return 0

    def collect_diagnostics(self, context: RunContext) -> tuple[Diagnostic, ...]:
        self.events.append("collect_diagnostics")
        return ()

    def stop(self, context: RunContext) -> None:
        self.events.append("stop")
