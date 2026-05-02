"""Помощник повторных попыток со структурированным логированием."""

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
    """Контролируемая ошибка, возникающая при исчерпании всех попыток."""

    operation: str
    attempts: int
    reason: str

    # region Приватные
    def __str__(self) -> str:
        return (
            f"Источник не ответил на «{self.operation}» после {self.attempts} попыток: {self.reason}"
        )


    # endregion Приватные
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
    should_retry: Callable[[Exception], bool] | None = None,
    **kwargs: P.kwargs,
) -> R:
    """Выполняет вызов функции с повторами и структурированными логами каждой попытки."""
    if attempts < 1:
        raise ValueError("число попыток должно быть >= 1")

    target_logger = logger or logging.getLogger(__name__)
    endpoint_value = endpoint or "n/a"
    symbol_value = symbol or "n/a"

    def _compute_sleep_seconds(exc: Exception, attempt_number: int) -> float:
        message = str(exc).lower()
        if "too many requests" in message or "429" in message or "-1003" in message:
            return max(backoff_seconds * attempt_number, 20.0)
        return backoff_seconds

    for attempt_number in range(1, attempts + 1):
        try:
            result = call(*args, **kwargs)
            target_logger.debug(
                "Источник ответил на «%s» с попытки %s/%s. Endpoint: %s. Символ: %s.",
                operation,
                attempt_number,
                attempts,
                endpoint_value,
                symbol_value,
            )
            return result
        except retriable_exceptions as exc:
            if should_retry is not None and not should_retry(exc):
                target_logger.warning(
                    "Запрос «%s» остановлен без повтора. Попытка %s/%s. Endpoint: %s. Символ: %s. Причина: %s",
                    operation,
                    attempt_number,
                    attempts,
                    endpoint_value,
                    symbol_value,
                    exc,
                )
                raise
            is_last = attempt_number >= attempts
            target_logger.warning(
                "Источник не ответил на «%s». Попытка %s/%s. Endpoint: %s. Символ: %s. Причина: %s",
                operation,
                attempt_number,
                attempts,
                endpoint_value,
                symbol_value,
                exc,
            )
            if is_last:
                raise RetryExhaustedError(operation=operation, attempts=attempts, reason=str(exc)) from exc

            sleep_seconds = _compute_sleep_seconds(exc, attempt_number)
            if jitter_seconds and jitter_seconds > 0:
                sleep_seconds += random.uniform(0.0, jitter_seconds)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    raise RetryExhaustedError(
        operation=operation,
        attempts=attempts,
        reason="ответ так и не пришёл",
    )
