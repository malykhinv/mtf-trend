"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class PositionSignal:
    formation_timestamp_ms: int
    entry_price: Price
    entry_timestamp_ms: int
    stop_loss: Price
    take_profit_1: Price
    take_profit_2: Price
    position_side: PositionSide
    symbol: str
    breakout_timestamp_ms: int | None = None
    retest_timestamp_ms: int | None = None
    atr_bg: float | None = None
    high_pump: float | None = None
    tp1_close_ratio: float | None = None

    # region Приватные
    def __post_init__(self) -> None:
        if self.formation_timestamp_ms is None:
            msg = "Position signal formation_timestamp_ms is required."
            raise ValueError(msg)

        if self.formation_timestamp_ms < 0:
            msg = "Position signal formation_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if self.entry_timestamp_ms is None:
            msg = "Position signal entry_timestamp_ms is required."
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms < 0:
            msg = "Position signal breakout_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms < 0:
            msg = "Position signal retest_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if (
            self.breakout_timestamp_ms is not None
            and self.retest_timestamp_ms is not None
            and self.breakout_timestamp_ms > self.retest_timestamp_ms
        ):
            msg = (
                "Position signal invalid timestamp order: breakout_timestamp_ms must be <= "
                f"retest_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"retest_timestamp_ms={self.retest_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Position signal invalid timestamp order: breakout_timestamp_ms must be <= "
                f"entry_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Position signal invalid timestamp order: retest_timestamp_ms must be <= "
                f"entry_timestamp_ms, got retest_timestamp_ms={self.retest_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.formation_timestamp_ms > self.breakout_timestamp_ms:
            msg = (
                "Position signal invalid timestamp order: formation_timestamp_ms must be <= "
                f"breakout_timestamp_ms, got formation_timestamp_ms={self.formation_timestamp_ms}, "
                f"breakout_timestamp_ms={self.breakout_timestamp_ms}."
            )
            raise ValueError(msg)

        if not self.symbol:
            msg = "Position signal symbol is required."
            raise ValueError(msg)

        if self.position_side == PositionSide.LONG:
            if not (self.stop_loss.value < self.entry_price.value < self.take_profit_1.value):
                msg = "Invalid LONG signal levels: stop < entry < tp1 is required."
                raise ValueError(msg)
        else:
            if not (self.stop_loss.value > self.entry_price.value > self.take_profit_1.value):
                msg = "Invalid SHORT signal levels: stop > entry > tp1 is required."
                raise ValueError(msg)

        if self.position_side == PositionSide.LONG and self.take_profit_2.value < self.take_profit_1.value:
            msg = "For LONG, take_profit_2 must be >= take_profit_1."
            raise ValueError(msg)

        if self.position_side == PositionSide.SHORT and self.take_profit_2.value > self.take_profit_1.value:
            msg = "For SHORT, take_profit_2 must be <= take_profit_1."
            raise ValueError(msg)
    # endregion Приватные
