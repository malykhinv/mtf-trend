"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.trade_result_type import TradeResultType
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class TradeResult:
    entry_price: Price
    exit_price: Price
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    result_type: TradeResultType
    pnl: float
    pnl_percent: Percentage
    breakout_timestamp_ms: int | None = None
    retest_timestamp_ms: int | None = None

    # region Приватные
    def __post_init__(self) -> None:
        if self.entry_timestamp_ms is None or self.exit_timestamp_ms is None:
            msg = "Trade result entry/exit timestamp_ms is required."
            raise ValueError(msg)

        if self.exit_timestamp_ms < self.entry_timestamp_ms:
            msg = "Trade result exit_timestamp_ms cannot be earlier than entry_timestamp_ms."
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms < 0:
            msg = "Trade result breakout_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms < 0:
            msg = "Trade result retest_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if (
            self.breakout_timestamp_ms is not None
            and self.retest_timestamp_ms is not None
            and self.breakout_timestamp_ms > self.retest_timestamp_ms
        ):
            msg = (
                "Trade result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"retest_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"retest_timestamp_ms={self.retest_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Trade result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"entry_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms > self.entry_timestamp_ms:
            msg = (
                "Trade result invalid timestamp order: retest_timestamp_ms must be <= "
                f"entry_timestamp_ms, got retest_timestamp_ms={self.retest_timestamp_ms}, "
                f"entry_timestamp_ms={self.entry_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.breakout_timestamp_ms is not None and self.breakout_timestamp_ms > self.exit_timestamp_ms:
            msg = (
                "Trade result invalid timestamp order: breakout_timestamp_ms must be <= "
                f"exit_timestamp_ms, got breakout_timestamp_ms={self.breakout_timestamp_ms}, "
                f"exit_timestamp_ms={self.exit_timestamp_ms}."
            )
            raise ValueError(msg)

        if self.retest_timestamp_ms is not None and self.retest_timestamp_ms > self.exit_timestamp_ms:
            msg = (
                "Trade result invalid timestamp order: retest_timestamp_ms must be <= "
                f"exit_timestamp_ms, got retest_timestamp_ms={self.retest_timestamp_ms}, "
                f"exit_timestamp_ms={self.exit_timestamp_ms}."
            )
            raise ValueError(msg)
    # endregion Приватные
