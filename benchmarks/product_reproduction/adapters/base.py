from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

Profile = Literal["operator", "response-midpoint"]


@dataclass(frozen=True)
class RunContext:
    """Values owned by the orchestrator and safe for an adapter to consume."""

    run_id: str
    run_suffix: str
    target_id: str
    output_dir: Path
    working_dir: Path
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureEndpoint:
    """A capture endpoint that has already bound its ephemeral loopback port."""

    url: str
    host: str
    port: int
    correlation_id: str


@dataclass(frozen=True)
class ArtifactIdentity:
    reference: str
    identity: str
    version: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Diagnostic:
    category: str
    message: str
    level: Literal["info", "warning", "error"] = "info"


class ProductAdapter(ABC):
    """Lifecycle boundary between the neutral orchestrator and one product."""

    @abstractmethod
    def validate_host(self, context: RunContext) -> None:
        """Fail before acquisition when host prerequisites cannot be met."""

    @abstractmethod
    def acquire(self, context: RunContext) -> ArtifactIdentity:
        """Acquire reviewed release bytes and return their immutable identity."""

    @abstractmethod
    def render_config(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> Path:
        """Render typed, run-specific configuration from a checked-in template."""

    @abstractmethod
    def start(self, context: RunContext, profile: Profile, capture: CaptureEndpoint) -> None:
        """Start only resources whose names were assigned by the orchestrator."""

    @abstractmethod
    def wait_ready(self, context: RunContext) -> None:
        """Apply the product-specific readiness deadline and functional probe."""

    @abstractmethod
    def assert_identity(self, context: RunContext, artifact: ArtifactIdentity) -> None:
        """Prove that the acquired subject owns the measured endpoint."""

    @abstractmethod
    def measure_operator(self, context: RunContext) -> int:
        """Run the operator profile once and return its documented exit code."""

    @abstractmethod
    def measure_response_midpoint(self, context: RunContext) -> int:
        """Run the frozen one-seed midpoint response profile once."""

    @abstractmethod
    def collect_diagnostics(self, context: RunContext) -> tuple[Diagnostic, ...]:
        """Return bounded, sanitized diagnostic metadata."""

    @abstractmethod
    def stop(self, context: RunContext) -> None:
        """Stop only resources created for this run."""
