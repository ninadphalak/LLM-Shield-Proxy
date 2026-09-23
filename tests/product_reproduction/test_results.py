from __future__ import annotations

import pytest

from benchmarks.product_reproduction.results import (
    ExperimentHealth,
    ProductResult,
    ReproductionStatus,
    RunOutcome,
)


def test_complete_leaking_measurement_is_a_successful_experiment() -> None:
    outcome = RunOutcome(
        health=ExperimentHealth.COMPLETE,
        operator_result=ProductResult.LEAK,
        response_midpoint_result=ProductResult.LEAK,
        operator_reproduction=ReproductionStatus.MATCHED,
        response_midpoint_reproduction=ReproductionStatus.MATCHED,
    )

    assert outcome.workflow_conclusion == "success"
    assert outcome.product_results()["operator"] == "LEAK"
    assert outcome.reproduction_status()["operator"] == "matched"


def test_not_measured_experiment_fails_independently_of_product_outcome() -> None:
    outcome = RunOutcome(
        health=ExperimentHealth.NOT_MEASURED,
        operator_result=ProductResult.NOT_MEASURED,
        response_midpoint_result=ProductResult.NOT_MEASURED,
        operator_reproduction=ReproductionStatus.NOT_COMPARED,
        response_midpoint_reproduction=ReproductionStatus.NOT_COMPARED,
    )

    assert outcome.workflow_conclusion == "failure"


def test_complete_experiment_rejects_not_measured_profile() -> None:
    with pytest.raises(ValueError, match="complete experiment"):
        RunOutcome(
            health=ExperimentHealth.COMPLETE,
            operator_result=ProductResult.NOT_MEASURED,
            response_midpoint_result=ProductResult.CLEAN,
            operator_reproduction=ReproductionStatus.NOT_COMPARED,
            response_midpoint_reproduction=ReproductionStatus.MATCHED,
        )


def test_unhealthy_experiment_cannot_claim_reproduction_match() -> None:
    with pytest.raises(ValueError, match="unhealthy experiment"):
        RunOutcome(
            health=ExperimentHealth.INFRASTRUCTURE_ERROR,
            operator_result=ProductResult.NOT_MEASURED,
            response_midpoint_result=ProductResult.NOT_MEASURED,
            operator_reproduction=ReproductionStatus.MATCHED,
            response_midpoint_reproduction=ReproductionStatus.NOT_COMPARED,
        )


def test_complete_experiment_rejects_invalid_comparison() -> None:
    with pytest.raises(ValueError, match="invalid comparison"):
        RunOutcome(
            health=ExperimentHealth.COMPLETE,
            operator_result=ProductResult.CLEAN,
            response_midpoint_result=ProductResult.CLEAN,
            operator_reproduction=ReproductionStatus.INVALID,
            response_midpoint_reproduction=ReproductionStatus.NOT_COMPARED,
        )
