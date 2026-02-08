"""Retry helper with structured attempt logging."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(slots=True)
class RetryExhaustedError(Exception):
    """Controlled error raised when all retry attempts are exhausted."""

    operation: str
    attempts: int
    reason: str

    def __str__(self) -> str:
        return (
            f"Retry exhausted for operation='{self.operation}' after {self.attempts} attempts: "
            f"{self.reason}"
        )


def run_with_retry(
    operation: str,
    call: Callable[P, R],
    *args: P.args,
    attempts: int,
    backoff_seconds: float,
    retriable_exceptions: tuple[type[Exception], ...],
    logger: logging.Logger | None = None,
    endpoint: str | None = None,
    symbol: str | None = None,
    jitter_seconds: float | None = None,
    **kwargs: P.kwargs,
) -> R:
    """Execute callable with retries and structured logs for each attempt."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    target_logger = logger or logging.getLogger(__name__)
    endpoint_value = endpoint or "n/a"
    symbol_value = symbol or "n/a"

    for attempt_number in range(1, attempts + 1):
        try:
            result = call(*args, **kwargs)
            target_logger.info(
                "retry operation=%s attempt=%s/%s endpoint=%s symbol=%s result=success",
                operation,
                attempt_number,
                attempts,
                endpoint_value,
                symbol_value,
            )
            return result
        except retriable_exceptions as exc:
            is_last = attempt_number >= attempts
            target_logger.warning(
                "retry operation=%s attempt=%s/%s endpoint=%s symbol=%s result=failure reason=%s",
                operation,
                attempt_number,
                attempts,
                endpoint_value,
                symbol_value,
                exc,
            )
            if is_last:
                raise RetryExhaustedError(operation=operation, attempts=attempts, reason=str(exc)) from exc

            sleep_seconds = backoff_seconds
            if jitter_seconds and jitter_seconds > 0:
                sleep_seconds += random.uniform(0.0, jitter_seconds)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    raise RetryExhaustedError(
        operation=operation,
        attempts=attempts,
        reason="retry loop exited unexpectedly",
    )
