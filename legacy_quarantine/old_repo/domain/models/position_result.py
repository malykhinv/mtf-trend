"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_result_type import PositionResultType
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class PositionResult:
    entry_price: Price
    exit_price: Price
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    result_type: PositionResultType
    pnl: float
    pnl_percent: Percentage
    breakout_timestamp_ms: int | None = None
    retest_timestamp_ms: int | None = None
    pump_to_peak_bars: int | None = None
    pump_to_peak_minutes: float | None = None
    metadata: dict[str, int | float | str | bool | None] | None = None

    # region Приватные
    def __post_init__(self) -> None:
        if self.entry_timestamp_ms is None or self.exit_timestamp_ms is None:
            msg = "Position result entry/exit timestamp_ms is required."
            raise ValueError(msg)

        if self.exit_timestamp_ms < self.entry_timestamp_ms:
            msg = "Position result exit_timestamp_ms cannot be earlier than entry_timestamp_ms."
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms < 0:
            msg = "Position result breakout_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms < 0:
            msg = "Position result retest_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if (
            self.breakout_timestamp_ms is not None
            and self.retest_timestamp_ms is not None
            and self.breakout_timestamp_ms > self.retest_timestamp_ms
        ):
            msg = (
                "Position result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"retest_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"retest_timestamp_ms={self.retest_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Position result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"entry_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Position result invalid timestamp order: retest_timestamp_ms must be <= "
                f"entry_timestamp_ms, got retest_timestamp_ms={self.retest_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms > self.exit_timestamp_ms:
            msg = (
                "Position result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"exit_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"exit_timestamp_ms={self.exit_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms > self.exit_timestamp_ms:
            msg = (
                "Position result invalid timestamp order: retest_timestamp_ms must be <= "
                f"exit_timestamp_ms, got retest_timestamp_ms={self.retest_timestamp_ms}, "
                f"exit_timestamp_ms={self.exit_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.pump_to_peak_bars is not None and self.pump_to_peak_bars < 1:
            msg = f"Position result pump_to_peak_bars must be >= 1, got {self.pump_to_peak_bars}."
            raise ValueError(msg)

        if self.pump_to_peak_minutes is not None and self.pump_to_peak_minutes <= 0:
            msg = f"Position result pump_to_peak_minutes must be > 0, got {self.pump_to_peak_minutes}."
            raise ValueError(msg)

        if self.metadata is not None and not isinstance(self.metadata, dict):
            msg = "Position result metadata must be a dict when provided."
            raise ValueError(msg)
    # endregion Приватные
