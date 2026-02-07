"""Retest event model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models.breakout_event import BreakoutEvent
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class RetestEvent:
    breakout_event: BreakoutEvent
    retest_time: datetime
    retest_price: Price
    volume_retest: Volume
    oi_retest: Volume

    def __post_init__(self) -> None:
        if self.retest_time is None:
            msg = "Retest time is required."
            raise ValueError(msg)

        if self.retest_time < self.breakout_event.breakout_time:
            msg = "Retest time cannot be earlier than breakout time."
            raise ValueError(msg)
