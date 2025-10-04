from typing import Optional, Protocol

from domain.models import BalanceSource, ExecutionReport, MarginMode, Side, StopTrigger


class IExchangeTrade(Protocol):
    def get_balance(self, source: BalanceSource) -> float:
        ...

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        ...

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        ...

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        ...


__all__ = ["IExchangeTrade"]
