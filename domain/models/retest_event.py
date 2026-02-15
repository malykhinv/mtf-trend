"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.breakout_event import BreakoutEvent
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class RetestEvent:
    breakout_event: BreakoutEvent
    retest_timestamp_ms: int
    retest_price: Price
    volume_retest: Volume
    oi_retest: Volume

    # region Приватные
    def __post_init__(self) -> None:
        if not isinstance(self.retest_timestamp_ms, int):
            msg = "Retest timestamp must be int in unix milliseconds."
            raise TypeError(msg)

        if self.retest_timestamp_ms < 0:
            msg = "Retest timestamp must be non-negative."
            raise ValueError(msg)

        if self.retest_timestamp_ms < self.breakout_event.breakout_timestamp_ms:
            msg = "Retest timestamp cannot be earlier than breakout timestamp."
            raise ValueError(msg)
    # endregion Приватные
