from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")
CATEGORY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RetryableAcquisitionError(RuntimeError):
    """An idempotent release lookup, download, or image pull may be retried."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        if not CATEGORY_PATTERN.fullmatch(category):
            raise ValueError("retry category must be a lowercase safe identifier")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("Retry-After cannot be negative")
        self.category = category
        self.retry_after_seconds = retry_after_seconds


class AcquisitionRetryExhausted(RuntimeError):
    """The bounded acquisition policy cannot make another attempt."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    max_elapsed_seconds: float = 120.0
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds must be positive")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")


@dataclass(frozen=True)
class RetryAttempt:
    attempt: int
    category: str
    delay_seconds: float
    elapsed_seconds: float


def run_acquisition_with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> tuple[T, tuple[RetryAttempt, ...]]:
    started = monotonic()
    attempts: list[RetryAttempt] = []
    for attempt_number in range(1, policy.max_attempts + 1):
        try:
            return operation(), tuple(attempts)
        except RetryableAcquisitionError as exc:
            elapsed = max(0.0, monotonic() - started)
            if attempt_number >= policy.max_attempts:
                raise AcquisitionRetryExhausted(
                    f"acquisition exhausted {policy.max_attempts} attempts ({exc.category})"
                ) from exc
            if exc.retry_after_seconds is not None:
                delay = max(0.0, exc.retry_after_seconds)
            else:
                exponential = policy.base_delay_seconds * (2 ** (attempt_number - 1))
                delay = min(
                    policy.max_delay_seconds,
                    exponential * (0.5 + min(1.0, max(0.0, jitter()))),
                )
            if elapsed + delay > policy.max_elapsed_seconds:
                raise AcquisitionRetryExhausted(
                    f"acquisition retry would exceed elapsed-time limit ({exc.category})"
                ) from exc
            attempts.append(
                RetryAttempt(
                    attempt=attempt_number,
                    category=exc.category,
                    delay_seconds=delay,
                    elapsed_seconds=elapsed,
                )
            )
            sleep(delay)
    raise AssertionError("retry loop ended without returning or raising")
