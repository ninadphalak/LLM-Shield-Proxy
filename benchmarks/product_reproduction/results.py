from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExperimentHealth(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_MEASURED = "not-measured"
    INFRASTRUCTURE_ERROR = "infrastructure-error"
    RESOURCE_INSUFFICIENT = "resource-insufficient"


class ProductResult(str, Enum):
    CLEAN = "CLEAN"
    LEAK = "LEAK"
    CHECK_FAILED = "CHECK FAILED"
    NOT_MEASURED = "NOT MEASURED"


class ReproductionStatus(str, Enum):
    MATCHED = "matched"
    DRIFTED = "drifted"
    NOT_COMPARED = "not-compared"
    INVALID = "invalid"


@dataclass(frozen=True)
class RunOutcome:
    """Independent experiment, product, and baseline-comparison channels."""

    health: ExperimentHealth
    operator_result: ProductResult
    response_midpoint_result: ProductResult
    operator_reproduction: ReproductionStatus
    response_midpoint_reproduction: ReproductionStatus

    def __post_init__(self) -> None:
        results = (self.operator_result, self.response_midpoint_result)
        comparisons = (self.operator_reproduction, self.response_midpoint_reproduction)
        if self.health is ExperimentHealth.COMPLETE and ProductResult.NOT_MEASURED in results:
            raise ValueError("a complete experiment cannot contain a not-measured product result")
        if self.health is ExperimentHealth.COMPLETE and ReproductionStatus.INVALID in comparisons:
            raise ValueError("a complete experiment cannot contain an invalid comparison")
        if self.health not in (ExperimentHealth.COMPLETE, ExperimentHealth.PARTIAL) and any(
            status is ReproductionStatus.MATCHED for status in comparisons
        ):
            raise ValueError("an unhealthy experiment cannot claim a matched reproduction")

    @property
    def workflow_conclusion(self) -> str:
        return (
            "success"
            if self.health in (ExperimentHealth.COMPLETE, ExperimentHealth.PARTIAL)
            else "failure"
        )

    def product_results(self) -> dict[str, str]:
        return {
            "operator": self.operator_result.value,
            "response_midpoint": self.response_midpoint_result.value,
        }

    def reproduction_status(self) -> dict[str, str]:
        return {
            "operator": self.operator_reproduction.value,
            "response_midpoint": self.response_midpoint_reproduction.value,
        }
