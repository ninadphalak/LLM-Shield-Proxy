from __future__ import annotations

import secrets
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .adapters.base import Diagnostic, ProductAdapter, Profile, RunContext
from .catalog import RunnerRequirements
from .paths import validate_fresh_output_path
from .ports import LoopbackCapture
from .resources import (
    ResourceInsufficient,
    ResourceSnapshot,
    collect_resource_snapshot,
    validate_resource_admission,
)
from .retry import AcquisitionRetryExhausted, RetryAttempt, RetryPolicy, run_acquisition_with_retry
from .sanitizer import SensitiveValue, build_sanitizer

MAX_DIAGNOSTICS = 64
MAX_DIAGNOSTIC_CHARS = 2048


class ExperimentHealth(str, Enum):
    COMPLETE = "complete"
    NOT_MEASURED = "not-measured"
    INFRASTRUCTURE_ERROR = "infrastructure-error"
    RESOURCE_INSUFFICIENT = "resource-insufficient"


class EndpointOwnershipError(RuntimeError):
    """The measured endpoint is not owned by the acquired target."""


class TargetExited(RuntimeError):
    def __init__(self, exit_code: int) -> None:
        super().__init__(f"target exited with code {exit_code}")
        self.exit_code = exit_code


@dataclass(frozen=True)
class LifecycleOptions:
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    readiness_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.readiness_timeout_seconds <= 0:
            raise ValueError("readiness_timeout_seconds must be positive")


@dataclass(frozen=True)
class LifecycleResult:
    health: ExperimentHealth
    exit_codes: dict[str, int]
    diagnostics: tuple[str, ...]
    retry_attempts: tuple[RetryAttempt, ...]
    resource_snapshot: ResourceSnapshot
    run_suffix: str


ResourceProbe = Callable[[Path], ResourceSnapshot]
AdapterFactory = Callable[[], ProductAdapter]
CaptureFactory = Callable[..., LoopbackCapture]


def _existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise FileNotFoundError(f"no existing parent for {path}")
        candidate = parent
    return candidate


class ProductLifecycleRunner:
    def __init__(
        self,
        *,
        adapter_factory: AdapterFactory,
        resource_probe: ResourceProbe = collect_resource_snapshot,
        capture_factory: CaptureFactory = LoopbackCapture.start,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._resource_probe = resource_probe
        self._capture_factory = capture_factory
        self._monotonic = monotonic

    def run(
        self,
        *,
        target_id: str,
        runner_requirements: RunnerRequirements,
        output_dir: Path,
        repo_root: Path,
        sensitive_values: Iterable[SensitiveValue],
        options: LifecycleOptions = LifecycleOptions(),
        frozen_roots: Iterable[Path] = (),
    ) -> LifecycleResult:
        destination = validate_fresh_output_path(output_dir, repo_root=repo_root, frozen_roots=frozen_roots)
        sanitizer = build_sanitizer(tuple(sensitive_values))
        snapshot = self._resource_probe(_existing_parent(destination.parent))
        run_suffix = secrets.token_hex(8)
        try:
            validate_resource_admission(runner_requirements, snapshot)
        except ResourceInsufficient as exc:
            return LifecycleResult(
                health=ExperimentHealth.RESOURCE_INSUFFICIENT,
                exit_codes={},
                diagnostics=(sanitizer.sanitize(str(exc)),),
                retry_attempts=(),
                resource_snapshot=snapshot,
                run_suffix=run_suffix,
            )

        destination.mkdir(mode=0o700, parents=True)
        exit_codes: dict[str, int] = {}
        diagnostics: list[str] = []
        retry_attempts: tuple[RetryAttempt, ...] = ()
        health = ExperimentHealth.COMPLETE
        workspace_parent = destination.parent
        with tempfile.TemporaryDirectory(prefix="product-reproduction-work-", dir=workspace_parent) as temporary:
            workspace = Path(temporary)
            acquire_context = self._context(
                run_suffix=run_suffix,
                target_id=target_id,
                destination=destination,
                workspace=workspace / "acquire",
                profile=None,
                options=options,
            )
            acquire_context.working_dir.mkdir()
            acquisition_adapter = self._adapter_factory()
            try:
                acquisition_adapter.validate_host(acquire_context)
                artifact, retry_attempts = run_acquisition_with_retry(
                    lambda: acquisition_adapter.acquire(acquire_context),
                    policy=options.retry_policy,
                )
            except ResourceInsufficient as exc:
                diagnostics.extend(self._safe_diagnostics(acquisition_adapter, acquire_context, sanitizer.sanitize))
                diagnostics.append(sanitizer.sanitize(str(exc))[:MAX_DIAGNOSTIC_CHARS])
                return LifecycleResult(
                    health=ExperimentHealth.RESOURCE_INSUFFICIENT,
                    exit_codes=exit_codes,
                    diagnostics=tuple(diagnostics[:MAX_DIAGNOSTICS]),
                    retry_attempts=retry_attempts,
                    resource_snapshot=snapshot,
                    run_suffix=run_suffix,
                )
            except AcquisitionRetryExhausted as exc:
                diagnostics.extend(self._safe_diagnostics(acquisition_adapter, acquire_context, sanitizer.sanitize))
                diagnostics.append("acquisition retries exhausted")
                return LifecycleResult(
                    health=ExperimentHealth.INFRASTRUCTURE_ERROR,
                    exit_codes=exit_codes,
                    diagnostics=tuple(diagnostics[:MAX_DIAGNOSTICS]),
                    retry_attempts=exc.attempts,
                    resource_snapshot=snapshot,
                    run_suffix=run_suffix,
                )
            except Exception as exc:
                diagnostics.extend(self._safe_diagnostics(acquisition_adapter, acquire_context, sanitizer.sanitize))
                diagnostics.append(sanitizer.sanitize(f"acquisition failed: {type(exc).__name__}"))
                return LifecycleResult(
                    health=ExperimentHealth.INFRASTRUCTURE_ERROR,
                    exit_codes=exit_codes,
                    diagnostics=tuple(diagnostics[:MAX_DIAGNOSTICS]),
                    retry_attempts=retry_attempts,
                    resource_snapshot=snapshot,
                    run_suffix=run_suffix,
                )

            for profile in ("operator", "response-midpoint"):
                typed_profile: Profile = profile
                profile_workspace = workspace / profile
                profile_workspace.mkdir()
                context = self._context(
                    run_suffix=run_suffix,
                    target_id=target_id,
                    destination=destination,
                    workspace=profile_workspace,
                    profile=typed_profile,
                    options=options,
                )
                capture = self._capture_factory(correlation_id=f"{run_suffix}-{profile}")
                adapter = self._adapter_factory()
                start_attempted = False
                try:
                    adapter.render_config(context, typed_profile, capture.endpoint)
                    start_attempted = True
                    adapter.start(context, typed_profile, capture.endpoint)
                    adapter.wait_ready(context)
                    if self._monotonic() > context.readiness_deadline_monotonic:
                        raise TimeoutError("readiness deadline exceeded")
                    adapter.assert_identity(context, artifact)
                    if profile == "operator":
                        exit_code = adapter.measure_operator(context)
                    else:
                        exit_code = adapter.measure_response_midpoint(context)
                    exit_codes[profile] = exit_code
                    if exit_code == 2:
                        health = ExperimentHealth.NOT_MEASURED
                        break
                    if exit_code not in (0, 1):
                        health = ExperimentHealth.INFRASTRUCTURE_ERROR
                        break
                except EndpointOwnershipError:
                    diagnostics.append("endpoint ownership check failed")
                    health = ExperimentHealth.NOT_MEASURED
                    break
                except TargetExited as exc:
                    diagnostics.append(f"target exited with code {exc.exit_code}")
                    health = (
                        ExperimentHealth.RESOURCE_INSUFFICIENT
                        if exc.exit_code == 137
                        else ExperimentHealth.INFRASTRUCTURE_ERROR
                    )
                    break
                except Exception as exc:
                    diagnostics.append(sanitizer.sanitize(f"profile failed: {type(exc).__name__}"))
                    health = ExperimentHealth.INFRASTRUCTURE_ERROR
                    break
                finally:
                    diagnostics.extend(self._safe_diagnostics(adapter, context, sanitizer.sanitize))
                    if start_attempted:
                        try:
                            adapter.stop(context)
                        except Exception:
                            diagnostics.append("target cleanup failed")
                    capture.stop()

        return LifecycleResult(
            health=health,
            exit_codes=exit_codes,
            diagnostics=tuple(diagnostics[:MAX_DIAGNOSTICS]),
            retry_attempts=retry_attempts,
            resource_snapshot=snapshot,
            run_suffix=run_suffix,
        )

    def _context(
        self,
        *,
        run_suffix: str,
        target_id: str,
        destination: Path,
        workspace: Path,
        profile: Profile | None,
        options: LifecycleOptions,
    ) -> RunContext:
        return RunContext(
            run_id=run_suffix,
            run_suffix=run_suffix,
            target_id=target_id,
            output_dir=destination,
            working_dir=workspace,
            profile=profile,
            readiness_deadline_monotonic=self._monotonic() + options.readiness_timeout_seconds,
        )

    @staticmethod
    def _safe_diagnostics(
        adapter: ProductAdapter,
        context: RunContext,
        sanitize: Callable[[str], str],
    ) -> tuple[str, ...]:
        try:
            reported = adapter.collect_diagnostics(context)
        except Exception:
            return ("diagnostic collection failed",)
        return tuple(
            sanitize(_diagnostic_text(item))[:MAX_DIAGNOSTIC_CHARS]
            for item in reported[:MAX_DIAGNOSTICS]
        )


def _diagnostic_text(diagnostic: Diagnostic) -> str:
    return f"{diagnostic.level}:{diagnostic.category}:{diagnostic.message}"
