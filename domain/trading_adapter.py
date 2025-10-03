from __future__ import annotations

from typing import Protocol

from domain.models import BalanceSource, ExecutionReport, MarginMode, Side, StopTrigger


class TradingAdapter(Protocol):
    def get_balance(self, source: BalanceSource) -> float:
        ...

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> None:
        ...

    def place_market(self, side: Side, quantity: float) -> ExecutionReport:
        ...

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        ...


__all__ = ["TradingAdapter"]
