from __future__ import annotations

from typing import Optional

from domain.models import BalanceSource, Exchange, ExecutionReport, MarginMode, Side, StopTrigger
from domain.trading_adapter import TradingAdapter
from utils import get_current_time


class NoopTradingAdapter(TradingAdapter):
    def __init__(
        self,
        exchange: Exchange,
        symbol: str,
        *,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._exchange = exchange
        self._symbol = symbol
        self._balance = 1000.0
        self._api_key = api_key
        self._api_secret = api_secret

    def get_balance(self, source: BalanceSource) -> float:
        return self._balance

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: str | None = None,
    ) -> ExecutionReport:
        executed_at = get_current_time()
        if reason:
            # Preserve exit reason context for observability during development.
            print(f"[noop] market order reason: {reason}")
        return ExecutionReport(
            exchange=self._exchange,
            symbol=self._symbol,
            order_id=f"noop-{int(executed_at.timestamp())}",
            side=side,
            price=0.0,
            quantity=quantity,
            executed_qty=quantity,
            status="filled",
            commission=0.0,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> bool:
        return True


__all__ = ["NoopTradingAdapter"]
