from __future__ import annotations

import pytest

from benchmarks.product_reproduction.retry import (
    AcquisitionRetryExhausted,
    RetryableAcquisitionError,
    RetryPolicy,
    run_acquisition_with_retry,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_acquisition_retry_is_bounded_and_records_safe_metadata() -> None:
    clock = FakeClock()
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RetryableAcquisitionError("registry response included a secret", category="registry-timeout")
        return "sha256:resolved"

    result, attempts = run_acquisition_with_retry(
        operation,
        policy=RetryPolicy(max_attempts=3, max_elapsed_seconds=10, base_delay_seconds=1, max_delay_seconds=4),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        jitter=lambda: 0.5,
    )

    assert result == "sha256:resolved"
    assert calls == 3
    assert clock.sleeps == [1.0, 2.0]
    assert [attempt.category for attempt in attempts] == ["registry-timeout", "registry-timeout", None]
    assert [attempt.outcome for attempt in attempts] == ["retrying", "retrying", "succeeded"]
    assert all(attempt.operation_class == "artifact-acquisition" for attempt in attempts)
    assert "secret" not in repr(attempts)


def test_retry_after_is_honored_without_exceeding_elapsed_cap() -> None:
    clock = FakeClock()

    def operation() -> str:
        raise RetryableAcquisitionError("busy", category="rate-limit", retry_after_seconds=7)

    with pytest.raises(AcquisitionRetryExhausted, match="elapsed-time limit") as caught:
        run_acquisition_with_retry(
            operation,
            policy=RetryPolicy(max_attempts=3, max_elapsed_seconds=5, base_delay_seconds=1, max_delay_seconds=10),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            jitter=lambda: 0.5,
        )

    assert clock.sleeps == []
    assert caught.value.attempts[-1].outcome == "exhausted"
    assert caught.value.attempts[-1].category == "rate-limit"


def test_non_acquisition_exceptions_are_never_retried() -> None:
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("scored request failed")

    with pytest.raises(RuntimeError, match="scored request failed"):
        run_acquisition_with_retry(operation, policy=RetryPolicy())

    assert calls == 1


def test_jitter_never_exceeds_delay_cap() -> None:
    clock = FakeClock()
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableAcquisitionError("temporary", category="network-timeout")
        return "done"

    _, attempts = run_acquisition_with_retry(
        operation,
        policy=RetryPolicy(base_delay_seconds=10, max_delay_seconds=4),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        jitter=lambda: 1.0,
    )

    assert attempts[0].delay_seconds == 4


def test_attempt_exhaustion_retains_sanitized_attempt_history() -> None:
    clock = FakeClock()

    def operation() -> str:
        raise RetryableAcquisitionError("response carried sensitive detail", category="registry-timeout")

    with pytest.raises(AcquisitionRetryExhausted) as caught:
        run_acquisition_with_retry(
            operation,
            policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert [attempt.outcome for attempt in caught.value.attempts] == ["retrying", "exhausted"]
    assert "sensitive" not in repr(caught.value.attempts)
